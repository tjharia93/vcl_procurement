"""Pre-register permanent reel tags and record physical arrival, without posting stock.

Source documents own purchasing facts. Intake owns tag reservations and arrival
evidence until the Item is known; Batch and stock vouchers remain the stock system.
All responses are price-free. No endpoint submits or changes a stock transaction.
"""
import base64
import io
import json
from urllib.parse import quote

import frappe
from frappe import _
from frappe.utils import get_url, now_datetime, nowdate
from vcl_procurement.api.purchasing import _assert_purchasing_role
from vcl_procurement.reels import allocate_reel_ids, enable_reel_tracking
from vcl_procurement.reel_intake_core import arrival_date, clean_capture, clean_preparation, container_key, package_count

DT = 'Reel Intake'
READ_ROLES = {'Purchase Manager', 'Purchase User', 'Stock Manager', 'System Manager'}
FIELDS = ['name','status','supplier','supplier_invoice','purchase_invoice','purchase_order','shipment','container',
          'container_slot','material_description','gsm','item_code','width_mm','supplier_reel_no',
          'declared_net_kg','declared_gross_kg','measured_gross_kg','core_kg','received_on','received_by',
          'arrival_recorded_at','warehouse','notes','batch','purchase_receipt','modified']


def _gate():
    if not READ_ROLES.intersection(frappe.get_roles()):
        frappe.throw(_('Reel receiving is not available to your role.'), frappe.PermissionError)
    if not frappe.db.exists('DocType', DT):
        frappe.throw(_('Deploy vcl_procurement and run migrate to install reel tag registration.'))


def _source(invoice, shipment=None):
    inv = frappe.get_doc('Purchase Invoice', invoice)
    if inv.docstatus == 2:
        frappe.throw(_('The source invoice is cancelled.'))
    pos = sorted({r.purchase_order for r in inv.items if r.purchase_order})
    if len(pos) != 1:
        frappe.throw(_('Reel intake currently requires one linked Purchase Order per invoice.'))
    po = frappe.get_doc('Purchase Order', pos[0])
    po.check_permission('read')  # includes native Company/User Permissions
    if po.docstatus != 1 or po.company != inv.company or po.supplier != inv.supplier:
        frappe.throw(_('The source invoice and submitted Purchase Order do not match.'))
    if shipment:
        ship = frappe.get_doc('Import Shipment', shipment)
        if ship.purchase_order != po.name or container_key(ship.bl_number) != container_key(inv.get('custom_bill_of_lading_number')):
            frappe.throw(_('This shipment does not match the invoice and Purchase Order.'))
    return inv, po


def _rows(invoice, container=None):
    filters = {'purchase_invoice': invoice}
    if container:
        filters['container'] = container_key(container)
    return frappe.get_all(DT, filters=filters, fields=FIELDS, order_by='container asc, container_slot asc', limit_page_length=0)


def _shape(doc):
    return {key: doc.get(key) for key in FIELDS}


def _qr(path):
    from pyqrcode import create
    stream = io.BytesIO()
    create(get_url(path)).svg(stream, scale=4, quiet_zone=4)
    return 'data:image/svg+xml;base64,' + base64.b64encode(stream.getvalue()).decode()


@frappe.whitelist()
def delivery_page(invoice, shipment=None):
    _gate()
    inv, po = _source(invoice, shipment)
    if not shipment and frappe.db.exists('DocType','Import Shipment'):
        hits=frappe.get_all('Import Shipment',filters={'purchase_order':po.name},fields=['name','bl_number'],limit_page_length=100)
        matched=[r.name for r in hits if container_key(r.bl_number)==container_key(inv.get('custom_bill_of_lading_number'))]
        shipment=matched[0] if len(matched)==1 else None
    containers = [dict(number=container_key(c.container_reference), expected_reels=package_count(
        c.get('description_of_packaging_eg_51_reels_or_515_packages')), gross_kg=c.get('gross_weight'))
        for c in inv.get('custom_container_details_table') or [] if c.container_reference]
    return dict(invoice=inv.name, supplier=inv.supplier_name or inv.supplier, supplier_invoice=inv.bill_no,
                po=po.name, shipment=shipment, bl_number=inv.get('custom_bill_of_lading_number'),
                containers=containers, reels=_rows(inv.name),
                items=[dict(item_code=r.item_code,item_name=r.item_name,qty=r.qty,uom=r.uom) for r in inv.items],
                warehouses=frappe.get_list('Warehouse',filters={'company':po.company,'is_group':0,'disabled':0},pluck='name',limit_page_length=0),
                can_register=bool({'Purchase Manager','Purchase User','System Manager'}.intersection(frappe.get_roles())),
                stock_notice='Physical receiving only. Stock posting remains separate; existing stock units and history are unchanged.')


@frappe.whitelist(methods=['POST'])
def register_sizes(invoice, lines, shipment=None):
    """Reserve by known material counts; assign containers only when unloading."""
    _assert_purchasing_role()
    _gate()
    inv, po = _source(invoice,shipment)
    lines=json.loads(lines) if isinstance(lines,str) else lines
    try:
        plan=clean_preparation(lines,{r.item_code for r in inv.items})
    except (ValueError,AttributeError) as error:
        frappe.throw(str(error))
    expected=sum(package_count(c.get('description_of_packaging_eg_51_reels_or_515_packages')) for c in inv.get('custom_container_details_table') or [])
    if expected and sum(r['count'] for r in plan) != expected:
        frappe.throw(_('The material reel counts must equal the documented container total.'))
    frappe.db.sql('select name from `tabPurchase Invoice` where name=%s for update',(inv.name,))
    existing=_rows(inv.name)
    if existing:
        wanted={(r['item_code'],r['width_mm'],r['gsm']):r['count'] for r in plan}
        actual={}
        for row in existing:
            key=(row.item_code,row.width_mm,row.gsm)
            actual[key]=actual.get(key,0)+1
        if actual != wanted:
            frappe.throw(_('Tags already exist with a different material/count plan. Review before changing them.'))
        return dict(reused=True,reels=existing)
    if inv.docstatus == 1 and inv.update_stock:
        frappe.throw(_('This invoice already posted stock. Review before preparing incoming tags.'))
    ids=iter(allocate_reel_ids(sum(r['count'] for r in plan)))
    item_names={r.item_code:r.item_name for r in inv.items}
    result=[]
    for spec in plan:
        # Native Batch requires an enabled item. Never bypass legacy stock checks.
        item=frappe.get_doc('Item',spec['item_code'])
        if not item.has_batch_no and not frappe.db.count('Stock Ledger Entry',{'item_code':item.name}):
            enable_reel_tracking(item.name)
            item.reload()
        for slot_in_size in range(spec['count']):
            reel_id=next(ids)
            doc=frappe.get_doc(dict(doctype=DT,reel_id=reel_id,status='Expected',company=po.company,
                supplier=po.supplier,purchase_invoice=inv.name,supplier_invoice=inv.bill_no,purchase_order=po.name,
                shipment=shipment,container_slot=len(result)+1,material_description=item_names[spec['item_code']],
                item_code=spec['item_code'],width_mm=spec['width_mm'],gsm=spec['gsm'])).insert(ignore_permissions=True)
            if item.has_batch_no:
                batch=frappe.get_doc(dict(doctype='Batch',batch_id=reel_id,item=item.name,supplier=po.supplier,
                    reference_doctype=DT,reference_name=doc.name,
                    description='Expected reel; no stock received. '+po.name,
                    custom_reel_width_mm=doc.width_mm,custom_reel_gsm=doc.gsm,
                    custom_reel_purchase_invoice=inv.name,custom_reel_supplier_invoice=inv.bill_no,
                    custom_reel_purchase_order=po.name,custom_reel_bl_number=inv.get('custom_bill_of_lading_number'),
                    custom_reel_order_type='Import',custom_reel_verification='Pending verification')).insert(ignore_permissions=True)
                doc.batch=batch.name
                doc.save(ignore_permissions=True)
            result.append(_shape(doc))
    return dict(reused=False,reels=result)


@frappe.whitelist()
def reel_page(name):
    _gate()
    if not frappe.db.exists(DT, name):
        # Older printed Batch labels can use the same permanent Compass route.
        batch = frappe.get_doc('Batch', name)
        batch.check_permission('read')
        return dict(reel=dict(name=batch.name,status='Batch record',item_code=batch.item,batch=batch.name),
                    items=[],warehouses=[],stock_notice='Stock quantities are held in ERPNext’s stock transactions.')
    doc = frappe.get_doc(DT,name)
    inv, po = _source(doc.purchase_invoice,doc.shipment)
    return dict(reel=_shape(doc),items=[dict(item_code=r.item_code,item_name=r.item_name) for r in po.items],
                containers=[container_key(c.container_reference) for c in inv.get('custom_container_details_table') or [] if c.container_reference],
                warehouses=frappe.get_list('Warehouse',filters={'company':po.company,'is_group':0,'disabled':0},pluck='name',limit_page_length=0),
                stock_notice='Arrival confirmation does not post stock. Purchase Receipt posting is a separate step.')


def _warehouse(name, company):
    warehouse = frappe.get_doc('Warehouse',name)
    warehouse.check_permission('read')
    if warehouse.company != company or warehouse.is_group or warehouse.disabled:
        frappe.throw(_('Choose an active receiving warehouse for this company.'))


@frappe.whitelist(methods=['POST'])
def save_reel(name, fields, confirm_arrival=0, expected_modified=None):
    _gate()
    fields = json.loads(fields) if isinstance(fields,str) else fields
    if not isinstance(fields,dict):
        frappe.throw(_('Invalid reel details.'))
    frappe.db.sql('select name from `tabReel Intake` where name=%s for update',(name,))
    doc = frappe.get_doc(DT,name)
    _inv, po = _source(doc.purchase_invoice,doc.shipment)
    if expected_modified and str(doc.modified) != str(expected_modified):
        frappe.throw(_('This reel was changed by another receiver. Reload it before saving.'))
    if doc.purchase_receipt:
        frappe.throw(_('This reel is linked to stock. Review corrections through its stock documents.'))
    try:
        clean = clean_capture(fields,{r.item_code for r in po.items},nowdate())
    except ValueError as error:
        frappe.throw(str(error))
    if clean['supplier_reel_no']:
        frappe.db.sql('select name from `tabSupplier` where name=%s for update',(doc.supplier,))
        duplicate = frappe.db.exists(DT,{'supplier':doc.supplier,'supplier_reel_no':clean['supplier_reel_no'],'name':['!=',name]})
        if duplicate or frappe.db.exists('Batch',{'supplier':doc.supplier,'custom_supplier_reel_no':clean['supplier_reel_no'],'name':['!=',name]}):
            frappe.throw(_('This supplier reel number is already recorded.'))
    if doc.item_code and clean['item_code'] != doc.item_code:
        frappe.throw(_('This tag is prepared for a specific material. Use the matching tag or request a reviewed correction.'))
    if doc.width_mm and clean['width_mm'] != doc.width_mm:
        frappe.throw(_('The width must match the prepared tag. Put a mismatch on hold and record it in the notes.'))
    container=container_key(fields.get('container'))
    if container and container not in {container_key(c.container_reference) for c in _inv.get('custom_container_details_table') or []}:
        frappe.throw(_('Choose a container from this invoice.'))
    if doc.received_on and container != container_key(doc.container):
        frappe.throw(_('Container was already confirmed. Review the existing arrival before changing it.'))
    doc.container=container or None
    for key,value in clean.items():
        doc.set(key,value)
    if str(confirm_arrival) == '1':
        try:
            received = arrival_date(fields.get('received_on'),nowdate())
        except ValueError as error:
            frappe.throw(str(error))
        warehouse = str(fields.get('warehouse') or '')
        if not warehouse:
            frappe.throw(_('Choose the physical receiving warehouse.'))
        _warehouse(warehouse,po.company)
        # Do not let the receiving checklist silently exceed a documented container count.
        source=next((c for c in _inv.get('custom_container_details_table') or [] if container_key(c.container_reference)==container),None)
        limit=package_count(source.get('description_of_packaging_eg_51_reels_or_515_packages')) if source else 0
        frappe.db.sql('select name from `tabPurchase Invoice` where name=%s for update',(_inv.name,))
        if not doc.received_on and limit and frappe.db.count(DT,{'purchase_invoice':_inv.name,'container':container,'received_on':['is','set']}) >= limit:
            frappe.throw(_('This container already has its documented number of physical arrivals.'))
        if doc.received_on and (str(doc.received_on) != received or doc.warehouse != warehouse):
            frappe.throw(_('Arrival was already confirmed with a different date or warehouse. Review the existing record.'))
        doc.received_on=received
        doc.warehouse=warehouse
        if not doc.received_by:
            doc.received_by=frappe.session.user
            doc.arrival_recorded_at=now_datetime()
        doc.status='On hold' if fields.get('on_hold') else 'Arrived'
    elif doc.received_on:
        doc.status='On hold' if fields.get('on_hold') else 'Arrived'
    else:
        doc.status='On hold' if fields.get('on_hold') else 'Expected'
    doc.save(ignore_permissions=True)
    if doc.batch:
        batch=frappe.get_doc('Batch',doc.batch)
        if batch.reference_doctype in ('Purchase Receipt','Stock Entry'):
            frappe.throw(_('Stock transactions exist for this reel. Correct it through the stock workflow.'))
        batch.custom_supplier_reel_no=doc.supplier_reel_no
        batch.custom_declared_weight=doc.declared_net_kg or doc.declared_gross_kg
        batch.custom_reel_container=doc.container
        batch.save(ignore_permissions=True)
    return dict(reel=_shape(doc),stock_posted=False)


@frappe.whitelist()
def intake_labels(invoice, container=None, name=None, item_code=None):
    _gate()
    inv, _po = _source(invoice)
    rows = _rows(invoice,container)
    if name:
        rows=[r for r in rows if r.name == name]
    if item_code:
        rows=[r for r in rows if r.item_code == item_code]
    if not rows:
        frappe.throw(_('Register the reel identities before printing labels.'))
    result=[]
    for r in rows:
        shipment_containers=' / '.join(container_key(c.container_reference) for c in inv.get('custom_container_details_table') or [] if c.container_reference)
        result.append(dict(batch_id=r.name,item_code=r.item_code or '',item_name=r.material_description or 'Paper reel',
            supplier=inv.supplier_name or inv.supplier,supplier_reel_no=r.supplier_reel_no,
            supplier_invoice=inv.bill_no,purchase_order=r.purchase_order,bl_number=inv.get('custom_bill_of_lading_number'),
            container=r.container or shipment_containers,container_label='Container Number' if r.container else 'Shipment containers',
            order_type='Import',original_qty=r.declared_net_kg or r.declared_gross_kg or 0,
            weight_basis='Supplier label net weight' if r.declared_net_kg else 'Supplier label gross weight' if r.declared_gross_kg else 'Weight from physical label: net / gross',
            verification='Supplier declaration; verify on arrival',width_mm=r.width_mm,gsm=r.gsm,
            posting_date='',received_on=str(r.received_on or ''),pre_registered=True,physical_status=r.status,
            qr_uri=_qr('/compass/reels/'+quote(r.name,safe=''))))
    return dict(receipt=container or invoice,docstatus=0,reels=result)


@frappe.whitelist(methods=['POST'])
def confirm_delivery_arrival(invoice, names, received_on, warehouse):
    """Checked physical arrivals in one transaction; never a stock submission."""
    _gate()
    _inv, po = _source(invoice)
    names=json.loads(names) if isinstance(names,str) else names
    if not isinstance(names,list) or not names or len(names)>500 or not all(isinstance(n,str) for n in names) or len(set(names))!=len(names):
        frappe.throw(_('Select one or more distinct reel identities (maximum 500).'))
    try:
        received_on=arrival_date(received_on,nowdate())
    except ValueError as error:
        frappe.throw(str(error))
    _warehouse(warehouse,po.company)
    rows={r.name:r for r in _rows(invoice)}
    if any(name not in rows for name in names):
        frappe.throw(_('Every selected reel must belong to this delivery.'))
    changed=0
    for name in names:
        row=rows[name]
        if row.received_on:
            continue  # retry does not change previous arrival evidence
        fields={key:row.get(key) for key in ('item_code','width_mm','supplier_reel_no','declared_net_kg',
                'declared_gross_kg','measured_gross_kg','core_kg','container','notes')}
        fields.update(received_on=received_on,warehouse=warehouse,on_hold=row.status=='On hold')
        save_reel(name,fields,confirm_arrival=1)
        changed+=1
    return dict(arrivals_confirmed=changed,stock_posted=False)
