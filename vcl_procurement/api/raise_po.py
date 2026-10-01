"""Raising a purchase order, and receiving against one.

Phase 4 of the purchasing plan, brought forward on Tanuj's instruction, 4 Sep
2026: raise POs and receive goods here; **purchase invoices stay in the Desk**.
That is the right line. A PI carries four judgements per document — the supplier
invoice number, the exchange rate, whether the quantity billed matches what was
ordered, and whether the import tax stack belongs on it — and a button is the
wrong shape for a judgement.

Everything created here is a DRAFT. Submission stays a human act in the Desk,
the same standing rule as everywhere else.

**Prices.** A line takes `rate` and nothing else. The validate hook in
po_price_guard then makes it authoritative, so a PO raised through this API
cannot carry a hidden margin even if someone later edits it in the Desk. That is
the whole reason this exists rather than a Desk form on a small screen.

**Items are never created.** If a supplier is quoting something that is not in
the item master, that is a master-data decision, not something to do halfway up
a form on a phone. The search returns what exists and nothing else.
"""

import json

import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate, nowdate

from vcl_procurement.api.purchasing import _assert_purchasing_role

# A purchase is not a sale. Compass already has three `search_items`, all of
# them shaped for selling — item groups, product types, price lists. This one
# looks at what can be bought, which is a different set and a different filter.
ITEM_FIELDS = ["name", "item_name", "stock_uom", "item_group", "last_purchase_rate"]


def _default_schedule_date():
    """A week out. Long enough to be plausible, short enough to be corrected."""
    return add_days(nowdate(), 7)


@frappe.whitelist()
def search_suppliers(q="", limit=20):
    _assert_purchasing_role()
    filters = {"disabled": 0}
    if q:
        filters["name"] = ["like", f"%{q}%"]
    rows = frappe.get_all(
        "Supplier", filters=filters,
        fields=["name", "supplier_name", "supplier_group", "default_currency"],
        order_by="name asc", limit_page_length=int(limit))
    return [{
        "name": r.name,
        "label": r.supplier_name or r.name,
        "group": r.supplier_group,
        "currency": r.default_currency,
    } for r in rows]


@frappe.whitelist()
def search_purchase_items(q="", limit=20):
    """Items that can be bought. Never creates one."""
    _assert_purchasing_role()
    filters = {"disabled": 0, "is_purchase_item": 1}
    or_filters = None
    if q:
        or_filters = [["name", "like", f"%{q}%"], ["item_name", "like", f"%{q}%"]]
    rows = frappe.get_all(
        "Item", filters=filters, or_filters=or_filters, fields=ITEM_FIELDS,
        order_by="item_name asc", limit_page_length=int(limit))
    return [{
        "item_code": r.name,
        "item_name": r.item_name or r.name,
        "uom": r.stock_uom,
        "item_group": r.item_group,
        # Shown as a hint, never used as the price. The buyer types the rate
        # they were actually quoted — that is the entire point.
        "last_rate": flt(r.last_purchase_rate),
    } for r in rows]


@frappe.whitelist()
def pending_material_requests(limit=30):
    """Approved requests with something still to order.

    This is where most purchase orders should start. Somebody on the floor has
    already said what is needed and how much; retyping it into a phone is how
    the quantity changes on the way.
    """
    _assert_purchasing_role()
    rows = frappe.get_all(
        "Material Request",
        filters={"docstatus": 1, "material_request_type": "Purchase",
                 "status": ["not in", ["Stopped", "Cancelled"]],
                 "per_ordered": ["<", 100]},
        fields=["name", "transaction_date", "schedule_date", "status",
                "per_ordered", "owner"],
        order_by="transaction_date desc", limit_page_length=int(limit))
    return [{
        "name": r.name,
        "date": str(r.transaction_date) if r.transaction_date else None,
        "needed_by": str(r.schedule_date) if r.schedule_date else None,
        "status": r.status,
        "per_ordered": flt(r.per_ordered),
        "raised_by": r.owner,
    } for r in rows]


@frappe.whitelist()
def material_request_lines(name):
    """What is still to order on one request, ready to become PO lines."""
    _assert_purchasing_role()
    doc = frappe.get_doc("Material Request", name)
    doc.check_permission("read")

    out = []
    for it in doc.items:
        pending = flt(it.qty) - flt(it.ordered_qty)
        if pending <= 0:
            continue
        out.append({
            "item_code": it.item_code,
            "item_name": it.item_name or it.item_code,
            "qty": pending,
            "uom": it.uom,
            "warehouse": it.warehouse,
            "schedule_date": str(it.schedule_date) if it.schedule_date else None,
            "material_request": doc.name,
            "material_request_item": it.name,
            # No rate. A material request says what is needed, not what it
            # costs; carrying a guess through would be inventing a price.
            "rate": 0,
        })
    return {"name": doc.name, "lines": out}


@frappe.whitelist()
def create_purchase_order(supplier, lines, schedule_date=None, currency=None,
                          order_type="Local", order_confirmation_no=None, file_no=None,
                          payment_terms_template=None, tax_template=None,
                          order_date=None, note=None):
    """Create a DRAFT purchase order, complete, in ONE transaction.

    `lines` is a list of {item_code, qty, rate, uom?, conversion_factor?, warehouse?,
    material_request?, material_request_item?, description?}. `description` is plain text, written
    as editor HTML only when non-empty (else the item's own description is used). A unit other than the item's stock
    unit must arrive with its conversion.

    The confirmation number, file number (imports), payment terms and tax
    template used to be a second call from the browser, which could fail and leave a
    half-made order. They are applied here, so the order exists whole or not at all.

    `order_date` (default today) is the PO date and may not be after the schedule date;
    `note` is the free-text comment on the order (custom_comments__).
    """
    _assert_purchasing_role()
    if isinstance(lines, str):
        lines = json.loads(lines or "[]")
    if not supplier:
        frappe.throw(_("A supplier is required."))
    if not lines:
        frappe.throw(_("A purchase order needs at least one line."))

    wanted = getdate(schedule_date) if schedule_date else getdate(_default_schedule_date())

    from vcl_procurement.api import rules
    # A line in a unit other than the item's stock unit needs its conversion. The screen may
    # not send one (the older one never did), so look it up: the item's own rows, then the
    # site table. Only when nothing knows it is the line refused.
    from vcl_procurement.api.units import resolve_factor
    stock = {}
    for ln in lines:
        if ln.get("item_code") and ln.get("uom"):
            stock[ln["item_code"]], found = resolve_factor(ln["item_code"], ln["uom"])
            if not flt(ln.get("conversion_factor")) and found:
                ln["conversion_factor"] = found
    try:
        rules.check_line_units([ln for ln in lines if ln.get("uom")], stock)
    except rules.RuleError as e:
        frappe.throw(str(e))

    doc = frappe.new_doc("Purchase Order")
    doc.supplier = supplier
    doc.transaction_date = getdate(order_date) if order_date else nowdate()
    if order_date:      # only a date the caller chose is checked; today vs an old schedule date is not new
        try:
            rules.check_order_date(doc.transaction_date, wanted)
        except rules.RuleError as e:
            frappe.throw(str(e))
    doc.schedule_date = wanted
    if (note or "").strip():
        doc.custom_comments__ = note.strip()
    doc.custom_order_type = order_type
    if currency:
        doc.currency = currency

    for raw in lines:
        qty = flt(raw.get("qty"))
        if qty <= 0:
            frappe.throw(_("Every line needs a quantity."))
        if not raw.get("item_code"):
            frappe.throw(_("Every line needs an item."))

        row = doc.append("items", {})
        row.item_code = raw["item_code"]
        row.qty = qty
        row.schedule_date = getdate(raw.get("schedule_date") or wanted)
        if raw.get("uom"):
            row.uom = raw["uom"]
            if flt(raw.get("conversion_factor")) > 0:
                row.conversion_factor = flt(raw["conversion_factor"])
        if raw.get("warehouse"):
            row.warehouse = raw["warehouse"]
        if raw.get("material_request"):
            row.material_request = raw["material_request"]
            row.material_request_item = raw.get("material_request_item")

        if (raw.get("description") or "").strip():
            row.description = rules.html_from_text(raw["description"].strip())

        # The only price field. po_price_guard clears anything that could
        # re-derive it, so what is typed here is what gets ordered.
        row.rate = flt(raw.get("rate"))

    if order_confirmation_no:
        doc.order_confirmation_no = order_confirmation_no
    if payment_terms_template:
        doc.payment_terms_template = payment_terms_template
    if order_type == "Import":
        doc.tax_category = "Importation"
        if file_no:
            doc.custom_file_number = file_no
    if tax_template:
        from erpnext.controllers.accounts_controller import get_taxes_and_charges
        doc.taxes_and_charges = tax_template
        doc.set("taxes", [])
        for r in get_taxes_and_charges("Purchase Taxes and Charges Template", tax_template) or []:
            doc.append("taxes", r)

    doc.insert()
    return {
        "name": doc.name,
        "supplier": doc.supplier,
        "docstatus": doc.docstatus,
        "status": doc.status,
        "currency": doc.currency,
        "grand_total": flt(doc.grand_total),
        "lines": len(doc.items),
        "desk_url": f"/app/purchase-order/{doc.name}",
    }
