"""Reserve expected reel identities from a submitted PO, before invoicing.

Preparation creates Reel Intake records only. It never enables Item tracking,
creates Batches, records arrivals, or changes stock or purchasing quantities.
"""
import re

import frappe
from frappe import _
from vcl_procurement.api.purchasing import _assert_purchasing_role
from vcl_procurement.reel_intake import DT, FIELDS, _gate
from vcl_procurement.reels import allocate_reel_ids
from vcl_procurement.reels_core import number


def source(po, preparing=False):
    doc = frappe.get_doc('Purchase Order', po)
    doc.check_permission('read')
    if doc.docstatus != 1 or doc.status == 'Cancelled' or (preparing and doc.status == 'Closed'):
        frappe.throw(_('Prepare reels from an open, submitted Purchase Order.'))
    return doc


def rows(po):
    return frappe.get_all(DT, filters={'purchase_order': po},
        fields=FIELDS + ['purchase_order_item'], order_by='container_slot asc', limit_page_length=0)


def plan(doc):
    result = []
    for line in doc.items:
        raw = line.get('custom_no_of_reels')
        if isinstance(raw, bool):
            frappe.throw(_('Expected reels must be a whole number.'))
        if raw in (None, '', 0, 0.0):
            continue
        try:
            count = number(raw, 'Expected reels', positive=True)
        except ValueError as error:
            frappe.throw(str(error))
        if not count.is_integer() or count > 500 or not line.item_code or (line.qty or 0) <= 0:
            frappe.throw(_('Each reel line needs an item, a positive PO quantity and a whole reel count (1–500).'))
        text = re.sub('<[^>]+>', ' ', (line.get('description') or '') + ' ' + (line.item_name or ''))
        width = re.search(r'Reel\s+Width\s*:\s*(\d+(?:\.\d+)?)\s*mm', text, re.I)
        if not width:
            width = re.search(r'(\d+(?:\.\d+)?)\s*mm\b', line.item_name or '', re.I)
        if not width:
            frappe.throw(_('Record the reel width in mm on every PO reel line before preparing tags.'))
        gsm = re.search(r'\b(\d+(?:\.\d+)?)\s*gsm\b', text, re.I)
        try:
            width_mm = number(width[1], 'Reel width', positive=True)
            gsm_value = number(gsm[1], 'GSM', positive=True) if gsm else 0
        except ValueError as error:
            frappe.throw(str(error))
        result.append(dict(purchase_order_item=line.name, item_code=line.item_code,
            material_description=line.item_name, count=int(count), width_mm=width_mm,
            gsm=gsm_value, qty=line.qty, uom=line.uom))
    if not result or sum(r['count'] for r in result) > 500:
        frappe.throw(_('The PO must have documented reel counts totalling 1–500 reels.'))
    return result


@frappe.whitelist()
def order_page(po):
    _gate()
    doc = source(po)
    return dict(po=doc.name, supplier=doc.supplier_name or doc.supplier, company=doc.company,
        items=plan(doc), reels=rows(doc.name),
        can_register=doc.status != 'Closed' and bool({'Purchase Manager', 'Purchase User', 'System Manager'}.intersection(frappe.get_roles())),
        stock_notice='Expected reel identities only. Arrival details and stock posting remain separate.')


@frappe.whitelist(methods=['POST'])
def register(po):
    _assert_purchasing_role()
    _gate()
    doc = source(po, preparing=True)
    # Invoice preparation takes this same lock, so both routes cannot reserve
    # a different identity set for the same order concurrently.
    frappe.db.sql('select name from `tabPurchase Order` where name=%s for update', (doc.name,))
    doc.reload()
    doc = source(po, preparing=True)
    specs = plan(doc)
    existing = rows(doc.name)
    if existing:
        expected = {(r['purchase_order_item'], r['item_code'], r['width_mm'], r['gsm']): r['count'] for r in specs}
        actual = {}
        for row in existing:
            key = (row.purchase_order_item, row.item_code, row.width_mm, row.gsm or 0)
            actual[key] = actual.get(key, 0) + 1
        if expected != actual:
            frappe.throw(_('Reel identities already exist for this PO. Review them before changing the preparation plan.'))
        return dict(reused=True, reels=existing)
    if any((line.received_qty or 0) > 0 for line in doc.items if line.get('custom_no_of_reels')):
        frappe.throw(_('This PO already has received reels. Review existing identities before preparing tags.'))
    ids = iter(allocate_reel_ids(sum(r['count'] for r in specs)))
    slot = 0
    for spec in specs:
        for _index in range(spec['count']):
            slot += 1
            frappe.get_doc(dict(doctype=DT, reel_id=next(ids), status='Expected', company=doc.company,
                supplier=doc.supplier, purchase_order=doc.name, purchase_order_item=spec['purchase_order_item'],
                container_slot=slot,
                material_description=spec['material_description'], item_code=spec['item_code'],
                width_mm=spec['width_mm'], gsm=spec['gsm'])).insert(ignore_permissions=True)
    return dict(reused=False, reels=rows(doc.name))


def record_page(reel):
    """Read-only Step 2 view; the invoice receiving API must not edit PO-only tags."""
    doc = source(reel.purchase_order)
    if reel.company != doc.company or reel.supplier != doc.supplier:
        frappe.throw(_('The reel and source Purchase Order do not match.'))
    return dict(reel={key: reel.get(key) for key in FIELDS + ['purchase_order_item']},
        items=[], warehouses=[], containers=[], preparation_only=True,
        stock_notice='Expected reel prepared from Purchase Order. No arrival or stock receipt has been recorded.')
