"""Reel receiving: one Batch per physical reel, created with the Purchase Receipt.

A reel is a Batch. Paper is bought and valued in Kg, so the Batch carries the
reel's identity (ID, the mill's own reel number, weights, metres, inspection
state) and the stock ledger carries its quantity. Nothing here is a second
record of stock: the Batch has no quantity of its own, the Purchase Receipt row
does.

Reel IDs are `VCL-RL-<year>-<5 digits>`, continuing the sequence the intranet
`/reels/` tool was issuing (373 reels at cutover).

Two rules that are easy to get wrong:

* Batch tracking cannot be switched on for an item that already has stock
  without a reviewed Stock Reconciliation, because the existing stock would sit
  in no batch. `enable_reel_tracking` therefore refuses any item that has ever
  had a stock ledger entry.
* A reel line is received in the item's stock UOM (Kg). A fixed "reel" UOM
  conversion would be wrong on every reel: weights vary by up to 12% within a
  line.
"""
import json

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from vcl_procurement.api.purchasing import _assert_purchasing_role

REEL_ID_PREFIX = "VCL-RL"
# Last number issued by the intranet /reels/ tool before ERPNext took over.
REEL_ID_FLOOR = 373

DEPARTMENTS = "Carton\nComputer Paper\nWoodfree\nLabel"
INSPECTION = "Accepted\nPending inspection\nQuality hold"

REEL_FIELDS = {
    "Batch": [
        dict(fieldname="custom_reel_section", fieldtype="Section Break", label="Reel",
             insert_after="description"),
        dict(fieldname="custom_department", fieldtype="Select", label="Department",
             options=DEPARTMENTS, insert_after="custom_reel_section"),
        dict(fieldname="custom_supplier_reel_no", fieldtype="Data", label="Supplier reel no.",
             insert_after="custom_department", in_list_view=1, search_index=1),
        dict(fieldname="custom_inspection_status", fieldtype="Select", label="Inspection status",
             options=INSPECTION, default="Pending inspection",
             insert_after="custom_supplier_reel_no"),
        dict(fieldname="custom_reel_col", fieldtype="Column Break", insert_after="custom_inspection_status"),
        dict(fieldname="custom_declared_weight", fieldtype="Float", label="Declared net weight (kg)",
             description="From the supplier's packing list, before the reel is weighed.",
             insert_after="custom_reel_col"),
        dict(fieldname="custom_gross_weight", fieldtype="Float", label="Gross weight (kg)",
             insert_after="custom_declared_weight"),
        dict(fieldname="custom_core_weight", fieldtype="Float", label="Core weight (kg)",
             insert_after="custom_gross_weight"),
        dict(fieldname="custom_meters", fieldtype="Float", label="Metres", insert_after="custom_core_weight"),
        dict(fieldname="custom_labels_printed", fieldtype="Int", label="Labels printed", default="0",
             read_only=1, insert_after="custom_meters"),
    ]
}


def ensure_reel_fields():
    """Create or update the reel Custom Fields on Batch. Safe to run repeatedly."""
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

    create_custom_fields(REEL_FIELDS, ignore_validate=True, update=True)
    frappe.db.commit()


# --- reel IDs ---------------------------------------------------------------

def _last_reel_number(year):
    like = f"{REEL_ID_PREFIX}-{year}-%"
    row = frappe.db.sql(
        "select max(cast(substring_index(name, '-', -1) as unsigned)) from `tabBatch` where name like %s",
        (like,))
    top = int(row[0][0] or 0) if row else 0
    # The intranet tool's numbers were all issued in 2026.
    return max(top, REEL_ID_FLOOR) if str(year) == "2026" else top


def next_reel_ids(count, year=None):
    """The next `count` reel IDs, in order. Does not reserve them."""
    year = year or getdate(nowdate()).year
    start = _last_reel_number(year)
    return [f"{REEL_ID_PREFIX}-{year}-{start + i + 1:05d}" for i in range(int(count))]


# --- what the Receive page needs to know -----------------------------------

def _ledger_entries(item_code):
    return frappe.db.count("Stock Ledger Entry", {"item_code": item_code})


@frappe.whitelist()
def reel_lines(po):
    """For each PO line: is the item batch-tracked, and could it be?"""
    _assert_purchasing_role()
    doc = frappe.get_doc("Purchase Order", po)
    out = {}
    for row in doc.items:
        item = frappe.db.get_value("Item", row.item_code,
                                   ["has_batch_no", "stock_uom"], as_dict=True) or {}
        batch = bool(item.get("has_batch_no"))
        entries = 0 if batch else _ledger_entries(row.item_code)
        out[str(row.idx)] = {
            "item_code": row.item_code,
            "batch": batch,
            "can_enable": (not batch) and entries == 0,
            "uom": row.uom,
            "stock_uom": item.get("stock_uom"),
            "kg_ok": (row.uom == item.get("stock_uom")),
        }
    return {"lines": out, "next_ids": next_reel_ids(1)}


@frappe.whitelist()
def enable_reel_tracking(item_code):
    """Switch batch tracking on for an item that has never had stock."""
    _assert_purchasing_role()
    item = frappe.get_doc("Item", item_code)
    if item.has_batch_no:
        return {"item_code": item_code, "batch": True}
    if _ledger_entries(item_code):
        frappe.throw(_(
            "{0} already has stock history. Its existing stock would sit in no batch, so it needs "
            "a reviewed Stock Reconciliation before batch tracking can be switched on."
        ).format(item_code))
    item.has_batch_no = 1
    item.create_new_batch = 1
    item.save(ignore_permissions=True)
    return {"item_code": item_code, "batch": True}


# --- the receipt ------------------------------------------------------------

def _clean_reels(reels, label):
    out = []
    for n, raw in enumerate(reels or [], 1):
        gross, core = flt(raw.get("gross_weight")), flt(raw.get("core_weight"))
        net = flt(raw.get("net_weight")) or (gross - core)
        if net <= 0:
            frappe.throw(_("{0}: reel {1} has no net weight.").format(label, n))
        out.append({
            "supplier_reel_no": (raw.get("supplier_reel_no") or "").strip(),
            "gross_weight": gross, "core_weight": core, "net_weight": net,
            "meters": flt(raw.get("meters")),
            "declared_weight": flt(raw.get("declared_weight")),
            "inspection_status": raw.get("inspection_status") or "Pending inspection",
        })
    return out


@frappe.whitelist()
def receive_reels(po, lines, posting_date=None, supplier_delivery_note=None):
    """Create a DRAFT Purchase Receipt with one row, and one Batch, per reel.

    `lines` maps a PO line number (idx) to its reels:
        {"1": [{supplier_reel_no, gross_weight, core_weight, net_weight?, meters?,
                declared_weight?, inspection_status?}, ...]}
    Lines left out are not received. Draft only; submitting stays a human act.
    """
    _assert_purchasing_role()
    if isinstance(lines, str):
        lines = json.loads(lines or "{}")
    if not lines:
        frappe.throw(_("Add at least one reel."))

    from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt

    po_doc = frappe.get_doc("Purchase Order", po)
    by_idx = {str(r.idx): r for r in po_doc.items}
    pr = make_purchase_receipt(po)
    if posting_date:
        pr.posting_date = getdate(posting_date)
        pr.set_posting_time = 1
    if supplier_delivery_note:
        pr.supplier_delivery_note = supplier_delivery_note

    seen = set()
    plan = []  # (template PR row, PO row, [cleaned reels])
    for idx, reels in lines.items():
        po_row = by_idx.get(str(idx))
        if not po_row:
            frappe.throw(_("{0} has no line {1}.").format(po, idx))
        label = f"Line {idx} ({po_row.item_code})"
        item = frappe.db.get_value("Item", po_row.item_code, ["has_batch_no", "stock_uom"], as_dict=True)
        if not item or not item.has_batch_no:
            frappe.throw(_("{0}: reel tracking is not switched on for this item.").format(label))
        if po_row.uom != item.stock_uom:
            frappe.throw(_("{0}: reels are received in {1}, but this line is ordered in {2}.")
                         .format(label, item.stock_uom, po_row.uom))
        template = next((r for r in pr.items if r.purchase_order_item == po_row.name), None)
        if not template:
            frappe.throw(_("{0} has nothing left to receive.").format(label))
        cleaned = _clean_reels(reels, label)
        for reel in cleaned:
            key = (po_row.item_code, reel["supplier_reel_no"])
            if reel["supplier_reel_no"] and key in seen:
                frappe.throw(_("Reel {0} is entered twice for {1}.").format(reel["supplier_reel_no"], po_row.item_code))
            seen.add(key)
        plan.append((template, po_row, cleaned))

    total = sum(len(reels) for _t, _p, reels in plan)
    ids = iter(next_reel_ids(total))
    batches = []
    for template, po_row, reels in plan:
        for reel in reels:
            if reel["supplier_reel_no"] and frappe.db.exists("Batch", {
                    "item": po_row.item_code, "supplier": po_doc.supplier,
                    "custom_supplier_reel_no": reel["supplier_reel_no"]}):
                frappe.throw(_("Reel {0} from {1} has already been received for {2}.")
                             .format(reel["supplier_reel_no"], po_doc.supplier, po_row.item_code))
            batch = frappe.get_doc({
                "doctype": "Batch", "batch_id": next(ids), "item": po_row.item_code,
                "supplier": po_doc.supplier,
                "description": f"Received against {po}",
                "custom_supplier_reel_no": reel["supplier_reel_no"] or None,
                "custom_gross_weight": reel["gross_weight"], "custom_core_weight": reel["core_weight"],
                "custom_meters": reel["meters"], "custom_declared_weight": reel["declared_weight"],
                "custom_inspection_status": reel["inspection_status"],
            }).insert(ignore_permissions=True)
            batches.append((batch, template, reel))

    # One receipt row per reel, replacing the PO-wide row it came from.
    templates = {id(t): t for t, _p, _r in plan}
    pr.items = [r for r in pr.items if id(r) not in templates]
    for batch, template, reel in batches:
        row = pr.append("items", {})
        for key, value in template.as_dict().items():
            if key not in ("name", "idx", "parent", "parentfield", "parenttype", "doctype", "modified", "creation"):
                row.set(key, value)
        row.qty = row.received_qty = reel["net_weight"]
        row.stock_qty = reel["net_weight"]
        row.batch_no = batch.name
        row.use_serial_batch_fields = 1
        row.serial_and_batch_bundle = None
    for i, row in enumerate(pr.items, 1):
        row.idx = i

    pr.insert()
    for batch, _t, _r in batches:
        batch.db_set({"reference_doctype": "Purchase Receipt", "reference_name": pr.name})

    return {
        "name": pr.name, "docstatus": pr.docstatus, "po": po, "supplier": pr.supplier,
        "posting_date": str(pr.posting_date), "desk_url": f"/app/purchase-receipt/{pr.name}",
        "items": [{"item_code": b[1].item_code, "qty": b[2]["net_weight"], "uom": b[1].uom} for b in batches],
        "reels": [{"batch_id": b[0].name, "supplier_reel_no": b[2]["supplier_reel_no"],
                   "net_weight": b[2]["net_weight"], "item_code": b[1].item_code} for b in batches],
    }
