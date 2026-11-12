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
import hashlib
import re

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from vcl_procurement.api.purchasing import _assert_purchasing_role, _receipt_limits
from vcl_procurement.api import rules
from vcl_procurement.reels_core import clean_reel, receipt_conversion

REEL_ID_PREFIX = "VCL-RL"
# Last number issued by the intranet /reels/ tool before ERPNext took over.
# Verified read-only against intranet paper_reels on 2026-11-06 (441 reels).
REEL_ID_FLOOR = 441

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
        dict(fieldname="custom_declared_weight", fieldtype="Float", label="Supplier-declared weight (kg)",
             description="From the supplier's packing list; may include the core. Not a measured net weight.",
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

for fieldname, label, fieldtype, options in [
    ('custom_reel_original_qty', 'Original received quantity (kg)', 'Float', None),
    ('custom_reel_weight_basis', 'Weight basis', 'Select', 'Supplier-declared weight\nMeasured net weight'),
    ('custom_reel_verification', 'Weight verification', 'Select', 'Pending verification\nVerified'),
    ('custom_reel_purchase_invoice', 'Source purchase invoice', 'Link', 'Purchase Invoice'),
    ('custom_reel_supplier_invoice', 'Supplier invoice number', 'Data', None),
    ('custom_reel_purchase_order', 'Purchase order', 'Link', 'Purchase Order'),
    ('custom_reel_bl_number', 'BL number', 'Data', None),
    ('custom_reel_container', 'Container number', 'Data', None),
    ('custom_reel_order_type', 'Order type', 'Select', 'Local\nImport'),
    ('custom_reel_width_mm', 'Reel width (mm)', 'Float', None),
    ('custom_reel_gsm', 'GSM', 'Float', None),
    ('custom_reel_diameter_mm', 'Diameter (mm)', 'Float', None),
]:
    REEL_FIELDS['Batch'].append(dict(fieldname=fieldname, label=label, fieldtype=fieldtype,
        options=options, insert_after=REEL_FIELDS['Batch'][-1]['fieldname']))
REEL_FIELDS['Purchase Receipt'] = [
    dict(fieldname='custom_reel_source_invoice', label='Reel source invoice', fieldtype='Link',
         options='Purchase Invoice', insert_after='supplier_delivery_note', read_only=1),
    dict(fieldname='custom_reel_request_id', label='Reel receiving request', fieldtype='Data',
         hidden=1, unique=1, read_only=1),
    dict(fieldname='custom_reel_request_hash', label='Reel receiving fingerprint', fieldtype='Data',
         hidden=1, read_only=1),
]


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
    # Tags can be reserved before their material Item is known. Never reuse
    # those identities when the normal Purchase Receipt allocator runs.
    if frappe.db.exists('DocType', 'Reel Intake'):
        reserved = frappe.db.sql(
            "select max(cast(substring_index(name, '-', -1) as unsigned)) from `tabReel Intake` where name like %s",
            (like,))
        top = max(top, int(reserved[0][0] or 0) if reserved else 0)
    if frappe.db.exists('DocType', 'Physical Stock Unit'):
        reserved = frappe.db.sql(
            "select max(cast(substring_index(name, '-', -1) as unsigned)) from `tabPhysical Stock Unit` where name like %s",
            (like,))
        top = max(top, int(reserved[0][0] or 0) if reserved else 0)
    # The intranet tool's numbers were all issued in 2026.
    return max(top, REEL_ID_FLOOR) if str(year) == "2026" else top


def next_reel_ids(count, year=None):
    """The next `count` reel IDs, in order. Does not reserve them."""
    year = year or getdate(nowdate()).year
    start = _last_reel_number(year)
    return [f"{REEL_ID_PREFIX}-{year}-{start + i + 1:05d}" for i in range(int(count))]


def allocate_reel_ids(count, year=None):
    """Reserve IDs using Frappe's transaction-held Series row lock."""
    from frappe.model.naming import getseries
    year = year or getdate(nowdate()).year
    prefix = f'{REEL_ID_PREFIX}-{year}-'
    number = int(getseries(prefix, 5))
    floor = _last_reel_number(year)
    if number <= floor:
        frappe.db.sql('update `tabSeries` set current=%s where name=%s', (floor, prefix))
        number = int(getseries(prefix, 5))
    return [f'{prefix}{number:05d}'] + [prefix + getseries(prefix, 5) for _ in range(count-1)]


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
        try:
            factor = receipt_conversion(row.uom, item.get('stock_uom'), row.get('conversion_factor') or 1)
            reason = None
        except ValueError as e:
            factor, reason = 0, str(e)
        out[str(row.idx)] = {
            "item_code": row.item_code,
            "batch": batch,
            "can_enable": (not batch) and entries == 0,
            "uom": row.uom,
            "stock_uom": item.get("stock_uom"),
            'kg_ok': bool(factor), 'conversion_factor': factor, 'unsupported_reason': reason,
        }
    return {"lines": out, "next_ids": next_reel_ids(1), 'schema_version': 2}


@frappe.whitelist(methods=['POST'])
def enable_reel_tracking(item_code):
    """Switch batch tracking on for an item that has never had stock."""
    _assert_purchasing_role()
    item = frappe.get_doc("Item", item_code)
    if item.has_batch_no:
        return {"item_code": item_code, "batch": True}
    if _ledger_entries(item_code):
        frappe.throw(_(
            "{0} already has stock history. Its existing stock would sit in no batch, so it needs "
            "a reviewed migration or a linked replacement item before reel receiving can start."
        ).format(item_code))
    item.has_batch_no = 1
    item.create_new_batch = 1
    item.save(ignore_permissions=True)
    return {"item_code": item_code, "batch": True}


# --- the receipt ------------------------------------------------------------

def _clean_reels(reels, label):
    if not isinstance(reels, list) or not reels:
        frappe.throw(_('{0}: add at least one reel.').format(label))
    out = []
    for n, raw in enumerate(reels, 1):
        try:
            out.append(clean_reel(raw))
        except (ValueError, AttributeError) as e:
            frappe.throw(f'{label}: reel {n}: {e}')
    return out


@frappe.whitelist(methods=['POST'])
def receive_reels(po, lines, posting_date=None, supplier_delivery_note=None,
                  invoice=None, department=None, container=None, request_id=None):
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
    if not isinstance(lines, dict):
        frappe.throw(_('Reel lines must be keyed by PO line number.'))
    if department and department not in DEPARTMENTS.splitlines():
        frappe.throw(_('Choose a valid department.'))
    fingerprint = hashlib.sha256(json.dumps(dict(po=po,lines=lines,posting_date=posting_date,
        supplier_delivery_note=supplier_delivery_note,invoice=invoice,department=department,container=container),
        sort_keys=True).encode()).hexdigest()
    request_id = request_id or fingerprint
    if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,64}', request_id):
        frappe.throw(_('Invalid receiving request ID.'))
    # Serialise retries on this PO before reading the request key or making Batches.
    frappe.db.sql('select name from `tabPurchase Order` where name=%s for update', (po,))
    previous = frappe.db.get_value('Purchase Receipt', {'custom_reel_request_id':request_id},
                                   ['name','custom_reel_request_hash','docstatus'], as_dict=True)
    if not previous:
        previous = frappe.db.get_value('Purchase Receipt', {'custom_reel_request_hash':fingerprint,'docstatus':['<',2]},
                                       ['name','custom_reel_request_hash','docstatus'], as_dict=True)
    if previous:
        if previous.custom_reel_request_hash != fingerprint or previous.docstatus == 2:
            frappe.throw(_('This receiving request has changed or was cancelled. Start a new request.'))
        return _receipt_result(frappe.get_doc('Purchase Receipt', previous.name))

    from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt

    po_doc = frappe.get_doc("Purchase Order", po)
    po_doc.check_permission('read')
    if po_doc.docstatus != 1:
        frappe.throw(_('Receive against a submitted Purchase Order.'))
    source = _source_context(po_doc, invoice, container)
    by_idx = {str(r.idx): r for r in po_doc.items}
    pr = make_purchase_receipt(po)
    pr.custom_reel_source_invoice = invoice or None
    pr.custom_reel_request_id = request_id
    pr.custom_reel_request_hash = fingerprint
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
        try:
            receipt_conversion(po_row.uom, item.stock_uom, po_row.get('conversion_factor') or 1)
        except ValueError as e:
            frappe.throw(f'{label}: {e}')
        if invoice and po_row.name not in source['po_details']:
            frappe.throw(_('{0} is not on the source invoice.').format(label))
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

    _global, limits = _receipt_limits(po_doc)
    wanted = {r.idx:sum(x['stock_qty'] for x in rr) / receipt_conversion(r.uom,'Kg',r.get('conversion_factor') or 1)
              for _t,r,rr in plan}
    try:
        rules.check_receipt_lines(wanted,{idx:maximum for idx,(_pending,maximum) in limits.items()})
    except rules.RuleError as e:
        frappe.throw(str(e))
    total = sum(len(reels) for _t, _p, reels in plan)
    ids = iter(allocate_reel_ids(total))
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
                'custom_department': department,
                'custom_reel_original_qty': reel['stock_qty'],
                'custom_reel_weight_basis': reel['weight_basis'],
                'custom_reel_verification': reel['verification_status'],
                'custom_reel_purchase_invoice': invoice,
                'custom_reel_supplier_invoice': source['supplier_invoice'],
                'custom_reel_purchase_order': po,
                'custom_reel_bl_number': source['bl_number'],
                'custom_reel_container': source['container'],
                'custom_reel_order_type': source['order_type'],
                'custom_reel_width_mm': reel['width_mm'], 'custom_reel_gsm': reel['gsm'],
                'custom_reel_diameter_mm': reel['diameter_mm'],
            }).insert(ignore_permissions=True)
            batches.append((batch, template, reel))

    # One receipt row per reel, replacing the PO-wide row it came from.
    pr.items = []
    for batch, template, reel in batches:
        row = pr.append("items", {})
        for key, value in template.as_dict().items():
            if key not in ("name", "idx", "parent", "parentfield", "parenttype", "doctype", "modified", "creation"):
                row.set(key, value)
        factor = receipt_conversion(template.uom, 'Kg', template.get('conversion_factor') or 1)
        row.qty = row.received_qty = reel['stock_qty'] / factor
        row.conversion_factor = factor
        row.stock_qty = reel['stock_qty']
        row.batch_no = batch.name
        row.use_serial_batch_fields = 1
        row.serial_and_batch_bundle = None
    for i, row in enumerate(pr.items, 1):
        row.idx = i

    pr.insert()
    for batch, _t, _r in batches:
        batch.db_set({"reference_doctype": "Purchase Receipt", "reference_name": pr.name})

    return _receipt_result(pr)


def _source_context(po_doc, invoice=None, container=None):
    source = dict(supplier_invoice=None,bl_number=None,container=None,
                  order_type=po_doc.get('custom_order_type') or 'Local',po_details=[])
    if not invoice:
        if container:
            frappe.throw(_('Choose a source invoice before choosing a container.'))
        return source
    inv = frappe.get_doc('Purchase Invoice', invoice)
    inv.check_permission('read')
    if inv.docstatus == 2 or inv.supplier != po_doc.supplier or inv.company != po_doc.company:
        frappe.throw(_('The source invoice must be active and belong to the same supplier and company.'))
    if inv.docstatus == 1 and inv.get('update_stock'):
        frappe.throw(_('This invoice has already posted stock; do not receive it a second time.'))
    linked = [r.po_detail for r in inv.items if r.purchase_order == po_doc.name]
    if not linked:
        frappe.throw(_('The source invoice is not linked to this Purchase Order.'))
    containers = [str(r.container_reference).strip().upper() for r in
                  inv.get('custom_container_details_table') or [] if r.container_reference]
    chosen = str(container or '').strip().upper()
    if not chosen and len(containers) == 1:
        chosen = containers[0]
    if (chosen and chosen not in containers) or (len(containers)>1 and not chosen):
        frappe.throw(_('Choose a container from the source invoice.'))
    source.update(supplier_invoice=inv.bill_no, bl_number=inv.get('custom_bill_of_lading_number'),
        container=chosen or None,po_details=linked)
    if source['bl_number'] or containers:
        source['order_type'] = 'Import'
    return source


def _row_batch(row):
    if row.get('batch_no'):
        return row.batch_no
    if row.get('serial_and_batch_bundle'):
        return frappe.db.get_value('Serial and Batch Entry',{'parent':row.serial_and_batch_bundle},'batch_no')


def _receipt_result(pr):
    batches = [frappe.get_doc('Batch', batch) for row in pr.items if (batch := _row_batch(row))]
    return {
        "name": pr.name, "docstatus": pr.docstatus, "po": pr.items[0].purchase_order, "supplier": pr.supplier,
        "posting_date": str(pr.posting_date), "desk_url": f"/app/purchase-receipt/{pr.name}",
        'items': [dict(item_code=r.item_code,qty=r.qty,uom=r.uom) for r in pr.items],
        'reels': [dict(batch_id=b.name,supplier_reel_no=b.get('custom_supplier_reel_no'),
                      net_weight=b.get('custom_reel_original_qty') if b.get('custom_reel_weight_basis') == 'Measured net weight' else 0,
                      stock_qty=b.get('custom_reel_original_qty'),
                      weight_basis=b.get('custom_reel_weight_basis'),item_code=b.item) for b in batches],
    }


@frappe.whitelist()
def receipt_labels(name):
    """Price-free authorized identities; printing uses the shared server renderer."""
    import base64
    import io
    from urllib.parse import quote
    from pyqrcode import create
    from frappe.utils import get_url
    _assert_purchasing_role()
    pr = frappe.get_doc('Purchase Receipt', name)
    pr.check_permission('read')
    if pr.docstatus == 2:
        frappe.throw(_('Cancelled receipts cannot print active reel labels.'))
    result = []
    for row in pr.items:
        batch_id = _row_batch(row)
        if not batch_id:
            continue
        b = frappe.get_doc('Batch',batch_id)
        if b.reference_doctype != 'Purchase Receipt' or b.reference_name != name:
            frappe.throw(_('A reel does not belong to this receipt.'))
        stream = io.BytesIO()
        target = get_url('/compass/reels/' + quote(b.name, safe=''))
        create(target).svg(stream,scale=4,quiet_zone=4)
        result.append(dict(batch_id=b.name,item_code=b.item,item_name=row.item_name,
            supplier=pr.supplier_name or pr.supplier,supplier_reel_no=b.get('custom_supplier_reel_no'),
            supplier_invoice=b.get('custom_reel_supplier_invoice'),purchase_order=row.purchase_order,
            bl_number=b.get('custom_reel_bl_number'),container=b.get('custom_reel_container'),
            order_type=b.get('custom_reel_order_type'),original_qty=b.get('custom_reel_original_qty'),
            weight_basis=b.get('custom_reel_weight_basis'),verification=b.get('custom_reel_verification'),
            width_mm=b.get('custom_reel_width_mm'),gsm=b.get('custom_reel_gsm'),
            department=b.get('custom_department'),posting_date=str(pr.posting_date),
            qr_uri='data:image/svg+xml;base64,'+base64.b64encode(stream.getvalue()).decode()))
    return dict(receipt=name,docstatus=pr.docstatus,reels=result,source_type='Purchase Receipt',source_name=name)
