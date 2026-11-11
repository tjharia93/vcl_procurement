"""PO-linked IC identities and physical arrival evidence. No stock vouchers are posted."""
from urllib.parse import quote
import frappe
from frappe import _
from frappe.utils import nowdate, now_datetime
from vcl_procurement.container_core import preparation, allocation_key, capture, packing_change
from vcl_procurement.reel_intake import _qr

ROLES = {'Stock Manager', 'Purchase User', 'Purchase Manager', 'System Manager'}
NOTICE = 'Identity and physical checking only. Stock has not been posted by this screen.'
FIELDS = ['name', 'allocation', 'company', 'purchase_order', 'po_detail', 'item_code', 'item_name',
          'supplier', 'nominal_qty', 'stock_uom', 'status', 'supplier_lot', 'expiry_date',
          'received_qty', 'received_on', 'warehouse', 'checked_by', 'checked_at', 'notes',
          'safety_reference', 'batch', 'purchase_receipt', 'packing_revision', 'modified']


def _data(doc):
    row = {k: doc.get(k) for k in FIELDS}
    # Frappe Float defaults to zero; zero here means no physical quantity was captured.
    if not row['received_qty']:
        row['received_qty'] = None
    return row


def _gate():
    if not ROLES.intersection(frappe.get_roles()):
        frappe.throw(_('Container tracking is not available to your role.'), frappe.PermissionError)
    for dt in ('Container Allocation', 'Stock Container'):
        if not frappe.db.exists('DocType', dt):
            frappe.throw(_('Deploy vcl_procurement and run migrate to install container tracking.'))


def _source(po, invoice=None):
    doc = frappe.get_doc('Purchase Order', po)
    doc.check_permission('read')
    if doc.docstatus != 1:
        frappe.throw(_('Use a submitted, active Purchase Order.'))
    frappe.get_doc('Company', doc.company).check_permission('read')
    inv = None
    if invoice:
        inv = frappe.get_doc('Purchase Invoice', invoice)
        inv.check_permission('read')
        if inv.docstatus == 2 or inv.company != doc.company or inv.supplier != doc.supplier:
            frappe.throw(_('The source invoice must be active and match this PO supplier and company.'))
        if not any(r.purchase_order == po for r in inv.items):
            frappe.throw(_('This invoice is not linked to the selected Purchase Order.'))
    return doc, inv


def _item(code):
    item = frappe.get_doc('Item', code)
    item.check_permission('read')
    configured = frappe.conf.get('ic_container_item_groups') or ['Inks and Printing Chemicals']
    if not isinstance(configured, list) or not configured:
        frappe.throw(_('Configure ic_container_item_groups as a list of allowed Item Groups.'))
    group, seen = item.item_group, set()
    while group and group not in seen:
        if group in configured:
            if item.disabled or not item.is_stock_item:
                frappe.throw(_('Choose an active stock item.'))
            return item
        seen.add(group)
        group = frappe.db.get_value('Item Group', group, 'parent_item_group')
    frappe.throw(_('This item is outside the configured ink and chemical groups.'))


def _rows(key, include_replaced=False):
    rows = frappe.get_all('Stock Container', filters={'allocation': key}, fields=FIELDS,
                         order_by='name asc', limit_page_length=0)
    return [_data(r) for r in rows if include_replaced or r['status'] != 'Replaced']


def _container(name):
    doc = frappe.get_doc('Stock Container', name)
    doc.check_permission('read')
    _source(doc.purchase_order)
    return doc


@frappe.whitelist()
def delivery_page(po, invoice=None):
    _gate()
    doc, inv = _source(po, invoice)
    lines = []
    for row in doc.items:
        # Native Item permissions are applied before returning line details.
        item = frappe.get_doc('Item', row.item_code)
        if not item.has_permission('read'):
            continue
        try:
            _item(item.name)
        except frappe.PermissionError:
            raise
        except frappe.ValidationError:
            continue
        if inv and not any(r.purchase_order == po and r.po_detail == row.name for r in inv.items):
            continue
        key = allocation_key(po, row.name)
        exists = frappe.db.exists('Container Allocation', key)
        if exists:
            allocation = frappe.get_doc('Container Allocation', key)
            allocation.check_permission('read')
        lines.append(dict(po_detail=row.name, item_code=row.item_code, item_name=row.item_name,
            stock_uom=item.stock_uom, ordered_stock_qty=row.stock_qty,
            allocation=key if exists else None,
            allocation_modified=str(allocation.modified) if exists else None,
            packing_revision=(allocation.get('revision') or 1) if exists else None,
            containers=_rows(key) if exists else [],
            replaced_containers=[r for r in _rows(key, True) if r['status'] == 'Replaced'] if exists else []))
    return dict(po=doc.name, company=doc.company, supplier=doc.supplier,
        supplier_name=doc.supplier_name, invoice=inv.name if inv else None, lines=lines,
        stock_notice=NOTICE, warehouses=frappe.get_list('Warehouse',
            filters={'company': doc.company, 'disabled': 0, 'is_group': 0}, pluck='name', limit_page_length=0))


@frappe.whitelist(methods=['POST'])
def register(po, po_detail, count, pack_qty, invoice=None):
    _gate()
    doc, inv = _source(po, invoice)
    row = next((r for r in doc.items if r.name == po_detail), None)
    if not row:
        frappe.throw(_('Choose a line from this Purchase Order.'))
    if inv and not any(r.purchase_order == po and r.po_detail == po_detail for r in inv.items):
        frappe.throw(_('This PO line is not on the source invoice.'))
    item = _item(row.item_code)
    try:
        plan = preparation(count, pack_qty, item.stock_uom, row.stock_qty)
    except ValueError as error:
        frappe.throw(str(error))
    # Serialize both PO-first and invoice-first requests on the original PO.
    frappe.db.sql('select name from `tabPurchase Order` where name=%s for update', (po,))
    doc.reload()
    row = next((r for r in doc.items if r.name == po_detail), None)
    if not row or doc.docstatus != 1:
        frappe.throw(_('The Purchase Order changed. Reload before preparing labels.'))
    item = _item(row.item_code)
    try:
        plan = preparation(count, pack_qty, item.stock_uom, row.stock_qty)
    except ValueError as error:
        frappe.throw(str(error))
    key = allocation_key(po, po_detail)
    if frappe.db.exists('Container Allocation', key):
        saved = frappe.get_doc('Container Allocation', key)
        saved.check_permission('read')
        if (saved.company != doc.company or saved.supplier != doc.supplier
                or saved.item_code != item.name or saved.stock_uom != plan['stock_uom']
                or saved.container_count != plan['count'] or abs(saved.nominal_qty-plan['pack_qty']) > 0.000001):
            frappe.throw(_('This PO line already has different prepared packing. Open its existing allocation; do not issue another set.'))
        rows = _rows(key)
        if len(rows) != (saved.get('active_container_count') or saved.container_count):
            frappe.throw(_('The saved allocation is incomplete. Ask a manager to review it before printing.'))
        if inv and not saved.purchase_invoice:
            saved.purchase_invoice = inv.name
            saved.save(ignore_permissions=True)
        return dict(allocation=key, reused=True, containers=rows, stock_posted=False)
    if row.received_qty or (inv and inv.docstatus == 1 and inv.get('update_stock')):
        frappe.throw(_('This line already has recorded receipts. Review existing stock before preparing new incoming identities.'))
    values = dict(company=doc.company, purchase_order=po, po_detail=po_detail,
                  item_code=item.name, item_name=item.item_name, supplier=doc.supplier,
                  stock_uom=item.stock_uom, nominal_qty=plan['pack_qty'])
    frappe.get_doc(dict(doctype='Container Allocation', allocation_id=key, **values,
        container_count=plan['count'], active_container_count=plan['count'], revision=1,
        purchase_invoice=invoice)).insert(ignore_permissions=True)
    from frappe.model.naming import getseries
    prefix = 'VCL-IC-' + str(nowdate())[:4] + '-'
    for _n in range(plan['count']):
        frappe.get_doc(dict(doctype='Stock Container', container_id=prefix+getseries(prefix, 5),
            allocation=key, status='Expected', packing_revision=1, **values)).insert(ignore_permissions=True)
    return dict(allocation=key, reused=False, containers=_rows(key), stock_posted=False)


@frappe.whitelist(methods=['POST'])
def revise_packing(po, po_detail, count, pack_qty, reason, expected_modified, invoice=None):
    _gate()
    doc, inv = _source(po, invoice)
    frappe.db.sql('select name from `tabPurchase Order` where name=%s for update', (po,))
    doc.reload()
    row = next((r for r in doc.items if r.name == po_detail), None)
    if not row or doc.docstatus != 1:
        frappe.throw(_('Reload the active Purchase Order before changing packing.'))
    if inv and not any(r.purchase_order == po and r.po_detail == po_detail for r in inv.items):
        frappe.throw(_('This PO line is not on the source invoice.'))
    key = allocation_key(po, po_detail)
    saved = frappe.get_doc('Container Allocation', key)
    saved.check_permission('read')
    item = _item(row.item_code)
    if saved.company != doc.company or saved.supplier != doc.supplier or saved.item_code != item.name or saved.stock_uom != item.stock_uom:
        frappe.throw(_('The order material changed. Review its existing identities before changing packing.'))
    # The original PO lock serializes preparation and every revision of this allocation.
    active = _rows(key)
    replaceable = [r for r in active if r['status'] == 'Expected' and not any(
        r.get(k) for k in ('received_on', 'received_qty', 'warehouse', 'supplier_lot', 'purchase_receipt'))]
    replacing = {r['name'] for r in replaceable}
    retained = [r for r in active if r['name'] not in replacing]
    retained_qty = sum(float(r.get('received_qty') or r['nominal_qty']) for r in retained)
    try:
        plan, fingerprint = packing_change(count, pack_qty, item.stock_uom, row.stock_qty,
            retained_qty, reason, expected_modified)
    except ValueError as error:
        frappe.throw(str(error))
    if not expected_modified or str(saved.modified) != str(expected_modified):
        if expected_modified and saved.get('last_packing_change_hash') == fingerprint:
            return dict(allocation=key, reused=True, containers=active, stock_posted=False)
        frappe.throw(_('This packing allocation changed. Reload before changing the remaining containers.'))
    if row.received_qty or (inv and inv.docstatus == 1 and inv.get('update_stock')):
        frappe.throw(_('This line has posted stock. Review its receipt links before changing the remaining packing.'))
    if len(replaceable) == plan['count'] and all(abs(float(r['nominal_qty'])-plan['pack_qty']) < 0.000001 for r in replaceable):
        return dict(allocation=key, reused=True, containers=active, stock_posted=False)
    # Never change an arrived/held/posted container or reuse its ID.
    for r in replaceable:
        old = frappe.get_doc('Stock Container', r['name'])
        old.status = 'Replaced'
        old.notes = 'Unused packing replaced: ' + reason.strip()
        old.save(ignore_permissions=True)
    from frappe.model.naming import getseries
    revision = int(saved.get('revision') or 1) + 1
    prefix = 'VCL-IC-' + str(nowdate())[:4] + '-'
    for _n in range(plan['count']):
        frappe.get_doc(dict(doctype='Stock Container', container_id=prefix+getseries(prefix, 5),
            allocation=key, company=doc.company, purchase_order=po, po_detail=po_detail,
            item_code=item.name, item_name=item.item_name, supplier=doc.supplier,
            stock_uom=item.stock_uom, nominal_qty=plan['pack_qty'], status='Expected',
            packing_revision=revision)).insert(ignore_permissions=True)
    saved.revision = revision
    saved.revision_reason = reason.strip()
    saved.active_container_count = len(retained) + plan['count']
    saved.last_packing_change_hash = fingerprint
    saved.save(ignore_permissions=True)
    return dict(allocation=key, reused=False, containers=_rows(key), replaced_count=len(replaceable), stock_posted=False)


@frappe.whitelist()
def container_page(name):
    _gate()
    doc = _container(name)
    item = frappe.get_doc('Item', doc.item_code)
    item.check_permission('read')
    po, _inv = _source(doc.purchase_order)
    return dict(container=_data(doc), stock_notice=NOTICE,
        expiry_required=bool(item.get('has_expiry_date') or item.get('shelf_life_in_days')),
        warehouses=frappe.get_list('Warehouse', filters={'company': po.company, 'disabled': 0,
            'is_group': 0}, pluck='name', limit_page_length=0))


@frappe.whitelist(methods=['POST'])
def check_container(name, action, expected_modified, received_qty=None, supplier_lot='', expiry_date=None,
                    received_on=None, warehouse=None, notes='', safety_reference=''):
    _gate()
    doc = _container(name)
    # Serialize arrival checks with packing changes, so an ID being checked
    # cannot be retired from an earlier Expected snapshot in another request.
    frappe.db.sql('select name from `tabPurchase Order` where name=%s for update', (doc.purchase_order,))
    frappe.db.sql('select name from `tabStock Container` where name=%s for update', (name,))
    doc.reload()
    if not expected_modified or str(doc.modified) != str(expected_modified):
        frappe.throw(_('This container changed. Reload it before saving your check.'))
    if doc.purchase_receipt:
        frappe.throw(_('This container is linked to a stock receipt. Use its receipt controls for corrections.'))
    if doc.status == 'Replaced':
        frappe.throw(_('This unused identity was replaced after a packing change. Use one of the active container tags.'))
    item = frappe.get_doc('Item', doc.item_code)
    item.check_permission('read')
    try:
        fields = capture(action, received_qty, supplier_lot, expiry_date, received_on, str(nowdate()),
            bool(item.get('has_expiry_date') or item.get('shelf_life_in_days')), notes)
    except ValueError as error:
        frappe.throw(str(error))


    if action == 'verify' and not warehouse:
        frappe.throw(_('Choose the actual receiving warehouse.'))
    if warehouse:
        wh = frappe.get_doc('Warehouse', warehouse)
        wh.check_permission('read')
        if wh.company != doc.company or wh.disabled or wh.is_group:
            frappe.throw(_('Choose an active receiving warehouse in this company.'))
    if not isinstance(safety_reference, str) or len(safety_reference) > 140:
        frappe.throw(_('Keep the SDS or safety reference within 140 characters.'))
    if action == 'hold':
        fields = {k: v for k, v in fields.items() if k in ('status', 'notes') or v not in (None, '')}
    doc.update(fields)
    if action != 'hold' or warehouse:
        doc.warehouse = warehouse or None
    if action != 'hold' or safety_reference.strip():
        doc.safety_reference = safety_reference.strip()
    doc.checked_by = frappe.session.user
    doc.checked_at = now_datetime()
    doc.save(ignore_permissions=True)
    return dict(container=_data(doc), stock_posted=False)


@frappe.whitelist()
def print_labels(po, po_detail=None, name=None, format='label'):
    _gate()
    doc, _inv = _source(po)
    rows = []
    if name:
        record = _container(name)
        if record.purchase_order != doc.name:
            frappe.throw(_('This container belongs to another Purchase Order.'))
        rows = [_data(record)]
    else:
        for line in doc.items:
            if po_detail and line.name != po_detail:
                continue
            key = allocation_key(po, line.name)
            if frappe.db.exists('Container Allocation', key):
                frappe.get_doc('Container Allocation', key).check_permission('read')
                rows.extend(_rows(key))
    for row in rows:
        if row['status'] == 'Replaced':
            frappe.throw(_('Replaced identities cannot print active labels. Open the current packing allocation.'))
        row['supplier_name'] = doc.supplier_name or doc.supplier
        row['qr_uri'] = _qr('/compass/containers/' + quote(row['name'], safe=''))
    from vcl_procurement.inventory_labels import container_labels_html
    try:
        return dict(html=container_labels_html(rows, format))
    except ValueError as error:
        frappe.throw(str(error))


def container_native_print(name, single=False):
    _gate()
    if single:
        record = _container(name)
        html = print_labels(record.purchase_order, name=name)['html']
    else:
        record = frappe.get_doc('Container Allocation', name)
        record.check_permission('read')
        html = print_labels(record.purchase_order, po_detail=record.po_detail)['html']
    return html[html.index('<style>'):html.index('</style>')+8] + html[html.index('<article'):html.index('</body>')]
