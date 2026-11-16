"""Physical identities for a PO-linked receipt with aggregate, unbatched lines.

This is deliberately separate from Batch stock accounting. Registering tags
never edits a receipt, enables Item tracking, allocates stock, or submits it.
"""
import hashlib
import json
import re

import frappe
from frappe.utils import now_datetime, nowdate
from vcl_procurement.api.purchasing import _assert_purchasing_role
from vcl_procurement.reel_intake import DT, FIELDS, _gate, _qr, _warehouse
from vcl_procurement.reel_intake_core import arrival_date
from vcl_procurement.reels import allocate_reel_ids
from vcl_procurement.reels_core import number

PREFIX = 'Receipt tag preparation v1: '


def _source(name, write=False):
    _assert_purchasing_role()
    _gate()
    doc = frappe.get_doc('Purchase Receipt', name)
    doc.check_permission('write' if write else 'read')
    if doc.docstatus == 2 or doc.get('is_return'):
        frappe.throw('Use an active, non-return Purchase Receipt.')
    for po_name in sorted({r.purchase_order for r in doc.items if r.purchase_order}):
        po = frappe.get_doc('Purchase Order', po_name)
        po.check_permission('read')
        if po.company != doc.company or po.supplier != doc.supplier or po.docstatus != 1:
            frappe.throw('The source PO and receipt must have the same supplier and company.')
    return doc


def _rows(name):
    return frappe.get_all(DT, filters={'purchase_receipt': name},
        fields=FIELDS + ['purchase_order_item'], order_by='container_slot asc', limit_page_length=0)


def _snapshot(doc):
    payload = dict(company=doc.company, supplier=doc.supplier,
        date=str(doc.posting_date), delivery_note=doc.get('supplier_delivery_note') or '',
        items=[dict(name=r.name, item=r.item_code, po=r.purchase_order, po_line=r.purchase_order_item,
                    qty=r.qty, stock_qty=r.stock_qty, uom=r.uom, warehouse=r.warehouse,
                    batch=r.get('batch_no'),bundle=r.get('serial_and_batch_bundle')) for r in doc.items])
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _spec(row):
    text = re.sub('<[^>]+>', ' ', ' '.join(str(row.get(k) or '') for k in ('description','item_name','item_code')))
    width = re.search(r'Reel\s+Width\s*:\s*(\d+(?:\.\d+)?)', text, re.I)
    if not width:
        width = re.search(r'(\d+(?:\.\d+)?)\s*mm\b', text, re.I)
    gsm = re.search(r'\bGSM\s*:\s*(\d+(?:\.\d+)?)', text, re.I)
    if not gsm:
        gsm = re.search(r'\b(\d+(?:\.\d+)?)\s*GSM\b', text, re.I)
    return dict(width_mm=float(width[1]) if width else 0, gsm=float(gsm[1]) if gsm else 0)


def _eligible(row):
    item = frappe.get_doc('Item', row.item_code)
    item.check_permission('read')
    return (bool(item.get('is_stock_item')) and not item.get('has_batch_no') and not item.get('has_serial_no')
            and not row.get('batch_no') and not row.get('serial_and_batch_bundle')
            and bool(row.purchase_order and row.purchase_order_item) and (row.qty or 0) > 0)


def _plan(doc, lines):
    if any(r.get('batch_no') or r.get('serial_and_batch_bundle') for r in doc.items):
        frappe.throw('This receipt already has tracked stock rows. Use its existing reel assignment and labels.')
    if isinstance(lines, str):
        try:
            lines = json.loads(lines)
        except (ValueError, TypeError):
            frappe.throw('Enter the reel counts by receipt line.')
    if not isinstance(lines, list) or not lines or len(lines)>500:
        frappe.throw('Choose receipt lines and whole reel counts (maximum 500 reels).')
    source = {r.name:r for r in doc.items}
    result, seen = [], set()
    for raw in lines:
        if not isinstance(raw, dict) or raw.get('receipt_item') not in source:
            frappe.throw('Every reel must belong to a line of this receipt.')
        row = source[raw['receipt_item']]
        if row.name in seen or not _eligible(row):
            frappe.throw('Select each unbatched, PO-linked receipt line once. Use the existing reel workflow for tracked items.')
        seen.add(row.name)
        if any(isinstance(raw.get(k),bool) for k in ('count','width_mm','gsm')):
            frappe.throw('Enter numeric reel counts and specifications.')
        try:
            count = number(raw.get('count'), 'Reel count', positive=True)
            width = number(raw.get('width_mm'), 'Reel width', positive=True)
            gsm = number(raw.get('gsm'), 'GSM', positive=True)
        except ValueError as error:
            frappe.throw(str(error))
        if not count.is_integer() or count>500:
            frappe.throw('Reel counts must be whole numbers, up to 500.')
        _warehouse(row.warehouse, doc.company)
        po = frappe.get_doc('Purchase Order', row.purchase_order)
        match = next((r for r in po.items if r.name == row.purchase_order_item), None)
        if not match or match.item_code != row.item_code:
            frappe.throw('The receipt line does not match its source PO line.')
        result.append(dict(receipt_item=row.name, purchase_order=row.purchase_order,
            purchase_order_item=row.purchase_order_item, item_code=row.item_code,
            material_description=row.item_name or row.item_code, count=int(count),
            width_mm=width, gsm=gsm, warehouse=row.warehouse))
    if sum(r['count'] for r in result)>500:
        frappe.throw('Prepare a maximum of 500 reels per receipt.')
    if len({(r['purchase_order'],r['purchase_order_item']) for r in result})!=len(result):
        frappe.throw('Repeated PO lines require a reviewed reel assignment.')
    return sorted(result, key=lambda r:r['receipt_item'])


def _tag_matches(doc, tags, plan=None):
    signature = _snapshot(doc)
    current = {r.purchase_order_item:r for r in doc.items}
    for tag in tags:
        row = current.get(tag.purchase_order_item)
        try:
            saved = json.loads((tag.notes or '').removeprefix(PREFIX))
        except (ValueError, TypeError):
            frappe.throw('These receipt tags need a reviewed source assignment before printing.')
        if (saved.get('snapshot')!=signature or not row or saved.get('receipt_item')!=row.name
            or tag.purchase_order!=row.purchase_order or tag.item_code!=row.item_code):
            frappe.throw('The receipt changed after tag preparation. Review the tag assignment before printing or receiving.')
    if plan is not None:
        expected = {(r['purchase_order_item'],r['width_mm'],r['gsm']):r['count'] for r in plan}
        actual = {}
        for t in tags:
            key = (t.purchase_order_item,t.width_mm,t.gsm)
            actual[key] = actual.get(key,0)+1
        if expected != actual:
            frappe.throw('Tags already exist with different reel counts or specifications. Reuse them and review the difference.')


@frappe.whitelist()
def page(name):
    doc = _source(name)
    tags = _rows(name)
    if tags:
        _tag_matches(doc,tags)
    return dict(name=doc.name, expected_modified=str(doc.modified), docstatus=doc.docstatus,
        supplier=doc.supplier_name or doc.supplier, posting_date=str(doc.posting_date),
        delivery_note=doc.get('supplier_delivery_note'), reels=tags,
        can_prepare=doc.docstatus==0 and doc.has_permission('write'),
        lines=[dict(receipt_item=r.name, item_code=r.item_code, item_name=r.item_name,
            qty=r.qty, uom=r.uom, warehouse=r.warehouse, eligible=_eligible(r), **_spec(r)) for r in doc.items],
        stock_notice='Physical reel tags only. The receipt retains its combined line quantities; no individual Batch balances are created.')


@frappe.whitelist(methods=['POST'])
def prepare(name, lines, expected_modified):
    doc = _source(name,write=True)
    # Same PO-first order as PO/invoice preparation: never reserve another set
    # concurrently for the same purchasing source.
    for po in sorted({r.purchase_order for r in doc.items if r.purchase_order}):
        frappe.db.sql('select name from `tabPurchase Order` where name=%s for update',(po,))
    frappe.db.sql('select name from `tabPurchase Receipt` where name=%s for update',(name,))
    doc = _source(name,write=True)
    plan = _plan(doc,lines)
    tags = _rows(name)
    if tags:
        _tag_matches(doc,tags,plan)
        return dict(reused=True,stock_posted=False,**page(name))
    if doc.docstatus!=0:
        frappe.throw('Prepare physical reel tags on a draft receipt before submitting it.')
    if not expected_modified or str(doc.modified)!=str(expected_modified):
        frappe.throw('The receipt changed. Reload it and review the date, warehouse, quantities and counts.')
    try:
        received = arrival_date(str(doc.posting_date),nowdate())
    except ValueError as error:
        frappe.throw(str(error))
    for r in plan:
        if frappe.db.exists(DT,{'purchase_order':r['purchase_order'],
                'purchase_order_item':r['purchase_order_item'],'purchase_receipt':['is','not set']}):
            frappe.throw('Reel tags were already prepared from this purchasing source. Assign those identities; do not create duplicates.')
    ids = iter(allocate_reel_ids(sum(r['count'] for r in plan)))
    signature,slot = _snapshot(doc),0
    for spec in plan:
        for _ in range(spec['count']):
            slot+=1
            identity=next(ids)
            frappe.get_doc(dict(doctype=DT,reel_id=identity,status='Arrived',
                company=doc.company,supplier=doc.supplier,purchase_order=spec['purchase_order'],
                purchase_order_item=spec['purchase_order_item'],purchase_receipt=doc.name,
                container_slot=slot,item_code=spec['item_code'],material_description=spec['material_description'],
                width_mm=spec['width_mm'],gsm=spec['gsm'],warehouse=spec['warehouse'],
                received_on=received,received_by=frappe.session.user,arrival_recorded_at=now_datetime(),
                notes=PREFIX+json.dumps(dict(snapshot=signature,receipt_item=spec['receipt_item']),sort_keys=True))).insert(ignore_permissions=True)
    return dict(reused=False,stock_posted=False,**page(name))


def label_data(name, selected=None):
    from urllib.parse import quote
    doc = _source(name)
    tags = _rows(name)
    _tag_matches(doc,tags)
    if selected:
        tags = [t for t in tags if t.name==selected]
    if not tags:
        frappe.throw('Register the physical reel identities before printing.')
    return dict(receipt=name,docstatus=doc.docstatus,source_type='Purchase Receipt',source_name=name,
        reels=[dict(batch_id=t.name,item_code=t.item_code,item_name=t.material_description,
            supplier=doc.supplier_name or doc.supplier,purchase_order=t.purchase_order,order_type='Local',
            width_mm=t.width_mm,gsm=t.gsm,qr_uri=_qr('/compass/reels/'+quote(t.name,safe=''))) for t in tags])


@frappe.whitelist()
def labels(name):
    return label_data(name)


def record_page(tag):
    doc = _source(tag.purchase_receipt)
    _tag_matches(doc,[tag])
    return dict(reel={k:tag.get(k) for k in FIELDS+['purchase_order_item']},items=[],warehouses=[],
        containers=[],preparation_only=True,
        stock_notice=('Physical arrival recorded. Linked receipt '+doc.name+
            (' is still a draft; stock has not been posted.' if doc.docstatus==0 else ' is submitted with combined stock quantities.')+
            ' This tag has no individual Batch balance. Individual weights remain unverified until captured.'))
