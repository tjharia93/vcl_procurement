"""Register pre-existing reels/bundles and record custody without stock vouchers.

All writes use role + Company/document permissions, transactional locks and stale
versions. The registry links existing identities; it never replaces their IDs.
"""
import json
from html import escape
from urllib.parse import quote
import frappe
from frappe.utils import nowdate, now_datetime
from vcl_procurement import tagged_stock_core as rules

NOTICE = 'Physical custody only. This does not post opening stock, a stock transfer or valuation in ERPNext.'
UNIT = 'Physical Stock Unit'
REG = 'Stock Tag Registration'
MOVE = 'Tagged Stock Movement'
FIELDS = ['name','registration','company','kind','material','colour','width_mm','length_mm','gsm',
    'sheets_per_ream','reams_per_bundle','net_kg','weight_known','source_doctype','source_name',
    'status','warehouse','active_movement','last_event_on','last_checked_by','last_checked_at','notes','modified']
FIELDS += ['item_code','stock_uom','nominal_qty','observed_qty','quantity_known','fill_state',
           'supplier_name','supplier_lot','expiry_date','ledger_status']


def _gate():
    if not {'Stock Manager', 'System Manager'}.intersection(frappe.get_roles()):
        frappe.throw('Existing stock and physical movements require the Stock Manager role.', frappe.PermissionError)
    for dt in (UNIT, REG, MOVE):
        if not frappe.db.exists('DocType', dt):
            frappe.throw('Deploy vcl_procurement and run migrate to install existing-stock movements.')


def _validate(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (ValueError, TypeError, KeyError) as exc:
        frappe.throw(str(exc))


def _company(company):
    frappe.get_doc('Company', company).check_permission('read')


def _warehouse(name, company):
    doc = frappe.get_doc('Warehouse', name)
    doc.check_permission('read')
    if doc.company != company or doc.is_group or doc.disabled:
        frappe.throw('Choose an active storage warehouse in the same company.')


def _doc(dt, name, lock=False):
    doc = frappe.get_doc(dt, name)
    doc.check_permission('read')
    _company(doc.company)
    if lock:
        # dt is an internal constant, never user input.
        frappe.db.sql(f'select name from `tab{dt}` where name=%s for update', (name,))
        doc.reload()
    return doc


def _shape(doc):
    result = {key: doc.get(key) for key in FIELDS}
    if not result['weight_known']:
        result['net_kg'] = None
    if not result['quantity_known']:
        result['observed_qty'] = None
    return result


def _stale(doc, version):
    if not version or str(doc.modified) != str(version):
        frappe.throw('This record changed. Reload before saving.')


def _source(name, p):
    """Link an existing intake/bundle/batch only after checking its own scope."""
    if p['kind']=='Container':
        if frappe.db.exists('Stock Container',name):
            doc=frappe.get_doc('Stock Container',name);doc.check_permission('read')
            if doc.company!=p['company'] or doc.item_code!=p['item_code'] or doc.stock_uom!=p['stock_uom']:
                frappe.throw('The existing container belongs to another company or material/unit.')
            if doc.status!='Arrival checked' or doc.warehouse!=p['warehouse']:
                frappe.throw('This ID already belongs to a delivery. Confirm arrival at its recorded warehouse first, then reuse the same ID.')
            if float(doc.nominal_qty)!=p['nominal_qty'] or any(doc.get(k) and str(doc.get(k))!=str(p.get(k)) for k in ('supplier_lot','expiry_date')):
                frappe.throw('Reuse the original nominal pack, supplier lot and expiry for this existing ID.')
            return 'Stock Container',name
        return '', ''
    for dt in (('Reel Intake', 'Batch') if p['kind'] == 'Reel' else ('Stock Bundle',)):
        if not frappe.db.exists(dt, name):
            continue
        doc = frappe.get_doc(dt, name)
        doc.check_permission('read')
        if dt == 'Batch':
            # A Batch is not company-scoped. Require a readable original voucher.
            if not doc.get('reference_doctype') or not doc.get('reference_name'):
                frappe.throw('This Batch needs a manager to establish its source company before custody registration.')
            parent = frappe.get_doc(doc.reference_doctype, doc.reference_name)
            parent.check_permission('read')
            company = parent.get('company')
        else:
            company = doc.get('company')
        if company != p['company']:
            frappe.throw('The existing tag belongs to another company.')
        if doc.get('status') == 'On hold' or doc.get('custom_inspection_status') == 'Quality hold':
            frappe.throw('The existing tag is on hold. Resolve its inspection before registering it for movement.')
        if dt == 'Reel Intake' and not doc.get('received_on'):
            frappe.throw('This reel is an expected delivery. Confirm its arrival in the delivery screen first.')
        if dt == 'Reel Intake' and doc.get('warehouse') != p['warehouse']:
            frappe.throw('The reel arrival warehouse differs. Register at its recorded warehouse before moving it.')
        if doc.get('received_on'):
            _validate(rules.event_date,p['observed_on'],nowdate(),doc.received_on)
        if dt == 'Stock Bundle' and str(doc.get('colour') or '').strip().casefold() != p['colour'].casefold():
            frappe.throw('The colour differs from the existing bundle label. Review its original record.')
        if dt == 'Stock Bundle' and str(doc.get('material') or '').strip().casefold() != p['material'].casefold():
            frappe.throw('Use the material description on the existing bundle record.')
        for field in ('width_mm','gsm','length_mm','sheets_per_ream','reams_per_bundle'):
            old = doc.get(field) or (doc.get('custom_reel_'+field) if dt == 'Batch' else None)
            if old and float(old) != float(p.get(field) or 0):
                frappe.throw('The specifications differ from the existing tag. Review the original record first.')
        return dt, name
    return '', ''


def _source_hold(unit):
    if unit.kind=='Container' and unit.expiry_date and str(unit.expiry_date)<str(nowdate()):
        frappe.throw('This container is expired. Keep it on hold for review.')
    if unit.source_doctype and unit.source_name:
        source = frappe.get_doc(unit.source_doctype, unit.source_name)
        source.check_permission('read')
        if source.get('status') == 'On hold' or source.get('custom_inspection_status') == 'Quality hold' or (source.get('expiry_date') and str(source.expiry_date)<str(nowdate())):
            frappe.throw(f'{unit.name} is on hold in its original record.')


def _reserve_ids(p):
    from frappe.model.naming import getseries
    ids = p['existing_ids']
    if ids:
        # Same Series locks as all normal allocators. Advance high imported IDs so
        # a later purchase/bundle registration cannot issue them again.
        for prefix in sorted({name.rsplit('-', 1)[0]+'-' for name in ids}):
            getseries(prefix, 5)
            highest = max(int(name.rsplit('-', 1)[1]) for name in ids if name.startswith(prefix))
            frappe.db.sql('update `tabSeries` set current=greatest(current,%s) where name=%s', (highest, prefix))
        return ids
    if p['kind'] == 'Reel':
        from vcl_procurement.reels import allocate_reel_ids
        return allocate_reel_ids(p['count'])
    prefix = ('VCL-IC-' if p['kind']=='Container' else 'VCL-BND-')+str(nowdate())[:4]+'-'
    return [prefix+getseries(prefix, 5) for _ in range(p['count'])]


@frappe.whitelist()
def options():
    _gate()
    groups={g.name:g.parent_item_group for g in frappe.get_list('Item Group',fields=['name','parent_item_group'],limit_page_length=0)}
    allowed=frappe.conf.get('ic_container_item_groups') or ['Inks and Printing Chemicals']
    def eligible(group):
        seen=set()
        while group and group not in seen:
            if group in allowed:return True
            seen.add(group);group=groups.get(group)
        return False
    items=frappe.get_list('Item',filters={'disabled':0,'is_stock_item':1},fields=['name','item_name','stock_uom','item_group'],limit_page_length=0)
    return dict(companies=frappe.get_list('Company', pluck='name', limit_page_length=0),
        container_items=[i for i in items if i.stock_uom in ('Kg','Litre','Liter') and eligible(i.item_group)],
        warehouses=frappe.get_list('Warehouse', filters={'disabled':0,'is_group':0},
            fields=['name','company'], limit_page_length=0), stock_notice=NOTICE)


@frappe.whitelist(methods=['POST'])
def register(reference, plan, physically_verified=0):
    _gate()
    if str(physically_verified) != '1':
        frappe.throw('Confirm that these physical units were checked at the source and existing labels were inspected.')
    reference = _validate(rules.text, reference, 'Registration reference')
    p = _validate(rules.plan, plan, nowdate())
    if p['kind']=='Container':
        from vcl_procurement.container_tracking import _item
        item=_item(p['item_code'])
        if item.stock_uom!=p['stock_uom']:
            frappe.throw('The material unit changed. Reload and choose the item again.')
        p['material']=item.item_name
        if (item.get('has_expiry_date') or item.get('shelf_life_in_days')) and not p['expiry_date']:
            frappe.throw('This material requires an expiry date from its supplier label.')
        if p['expiry_date'] and p['expiry_date']<str(nowdate()):
            frappe.throw('This material is expired. Arrange a quality review before registering it as fit to move.')
    _company(p['company']); _warehouse(p['warehouse'], p['company'])
    frappe.db.sql('select name from `tabCompany` where name=%s for update', (p['company'],))
    if frappe.db.exists(REG, reference):
        saved = _doc(REG, reference)
        if saved.plan_json != rules.canonical(p):
            frappe.throw('This registration reference has different details. Open it rather than creating duplicate tags.')
        return dict(name=reference, reused=True, units=_registration_units(reference), stock_posted=False)
    ids = _reserve_ids(p)
    links = []
    for name in ids:
        if frappe.db.exists(UNIT, name):
            frappe.throw(f'{name} already has a custody record. Open it; do not register it again.')
        links.append(_source(name, p))
    frappe.get_doc(dict(doctype=REG, registration_id=reference, company=p['company'],
        warehouse=p['warehouse'], kind=p['kind'], observed_on=p['observed_on'],
        plan_json=rules.canonical(p))).insert(ignore_permissions=True)
    units = []
    for name, (source_dt, source_name) in zip(ids, links):
        values = {k:p.get(k) for k in ('company','kind','material','colour','width_mm','length_mm','gsm',
            'sheets_per_ream','reams_per_bundle','net_kg','warehouse','notes','item_code','stock_uom','nominal_qty',
            'observed_qty','quantity_known','fill_state','supplier_name','supplier_lot','expiry_date','ledger_status')}
        doc = frappe.get_doc(dict(doctype=UNIT,tag_id=name,registration=reference,
            status='Available',weight_known=p.get('net_kg') is not None,source_doctype=source_dt,
            source_name=source_name,last_event_on=p['observed_on'],last_checked_by=frappe.session.user,
            last_checked_at=now_datetime(),**values)).insert(ignore_permissions=True)
        units.append(_shape(doc))
    return dict(name=reference,reused=False,units=units,stock_posted=False)


def _registration_units(reference):
    return [_shape(r) for r in frappe.get_all(UNIT, filters={'registration':reference},fields=FIELDS,
        order_by='name asc',limit_page_length=0)]


@frappe.whitelist()
def register_page(reference):
    _gate(); doc = _doc(REG, reference)
    return dict(name=doc.name,plan=json.loads(doc.plan_json),units=_registration_units(reference),stock_notice=NOTICE)


@frappe.whitelist()
def browse(company=None, warehouse=None, query=''):
    _gate()
    filters = {}
    if company:
        _company(company); filters['company']=company
    if warehouse:
        filters['warehouse']=warehouse
    if query:
        filters['name']=['like','%'+str(query).strip()[:100]+'%']
    return dict(units=[_shape(r) for r in frappe.get_list(UNIT,filters=filters,fields=FIELDS,
        order_by='modified desc',limit_page_length=200)],
        registrations=frappe.get_list(REG,filters={'company':company} if company else {},
            fields=['name','warehouse','kind','observed_on'],order_by='creation desc',limit_page_length=50),
        movements=frappe.get_list(MOVE,filters={'company':company} if company else {},
            fields=['name','source_warehouse','target_warehouse','status','dispatched_on'],
            order_by='creation desc',limit_page_length=100),stock_notice=NOTICE)


@frappe.whitelist()
def unit_page(name):
    _gate(); doc = _doc(UNIT, name)
    # Child rows have no standalone permissions. Restrict to this checked company,
    # then check every parent before returning physical history.
    history=[]
    for row in frappe.get_all('Tagged Movement Unit',filters={'tag':name,'parenttype':MOVE},
            fields=['parent','outcome','received_on','note','checked_by','checked_at'],order_by='creation desc',limit_page_length=100):
        parent=_doc(MOVE,row.parent)
        history.append(dict(row,source_warehouse=parent.source_warehouse,target_warehouse=parent.target_warehouse,
            dispatched_on=parent.dispatched_on,custodian=parent.custodian))
    return dict(unit=_shape(doc),history=history,stock_notice=NOTICE)


@frappe.whitelist(methods=['POST'])
def dispatch(reference, company, source, target, dispatched_on, custodian, units, notes=''):
    _gate(); _company(company)
    reference=_validate(rules.text,reference,'Movement reference')
    custodian=_validate(rules.text,custodian,'Person responsible / vehicle',140)
    notes=_validate(rules.text,notes,'Dispatch notes',2000,False)
    _warehouse(source,company); _warehouse(target,company)
    if source == target:
        frappe.throw('Source and destination must be different warehouses.')
    rows=_validate(rules.selected,units)
    dispatched_on=_validate(rules.event_date,dispatched_on,nowdate())
    request=rules.canonical(dict(company=company,source=source,target=target,dispatched_on=dispatched_on,
        custodian=custodian,units=rows,notes=notes))
    frappe.db.sql('select name from `tabCompany` where name=%s for update',(company,))
    if frappe.db.exists(MOVE,reference):
        doc=_doc(MOVE,reference)
        if doc.request_json != request:
            frappe.throw('This movement reference has different details. Open the saved movement.')
        return dict(name=doc.name,reused=True,stock_posted=False)
    docs=[]
    for row in rows:
        unit=_doc(UNIT,row['name'],lock=True)
        _stale(unit,row['modified']); _source_hold(unit)
        if unit.company != company or unit.warehouse != source or unit.status != 'Available' or unit.active_movement:
            frappe.throw(f'{unit.name} is not available at the selected source warehouse.')
        _validate(rules.event_date,dispatched_on,nowdate(),unit.last_event_on)
        docs.append(unit)
    move=frappe.get_doc(dict(doctype=MOVE,movement_id=reference,company=company,
        source_warehouse=source,target_warehouse=target,dispatched_on=dispatched_on,custodian=custodian,
        notes=notes,request_json=request,status='In transit',dispatched_by=frappe.session.user,
        dispatched_at=now_datetime(),units=[dict(tag=d.name,outcome='Pending') for d in docs])).insert(ignore_permissions=True)
    for unit in docs:
        unit.status='In transit'; unit.warehouse=None; unit.active_movement=move.name
        unit.last_event_on=dispatched_on; unit.save(ignore_permissions=True)
    return dict(name=move.name,reused=False,stock_posted=False)


@frappe.whitelist()
def movement_page(name):
    _gate(); doc=_doc(MOVE,name)
    data={k:doc.get(k) for k in ('name','company','source_warehouse','target_warehouse','dispatched_on',
        'custodian','notes','status','dispatched_by','dispatched_at','modified')}
    data['units']=[]
    for row in doc.units:
        unit=_doc(UNIT,row.tag)
        data['units'].append(dict(tag=row.tag,outcome=row.outcome,received_on=row.received_on,
            checked_reams=row.checked_reams,note=row.note,checked_by=row.checked_by,checked_at=row.checked_at,
            kind=unit.kind,material=unit.material,reams_per_bundle=unit.reams_per_bundle,
            sheets_per_ream=unit.sheets_per_ream,net_kg=unit.net_kg if unit.weight_known else None,
            nominal_qty=unit.nominal_qty,observed_qty=unit.observed_qty if unit.quantity_known else None,stock_uom=unit.stock_uom))
    return dict(movement=data,stock_notice=NOTICE)


@frappe.whitelist(methods=['POST'])
def receive(name, tag, outcome, received_on, expected_modified, note='', checked_reams=None):
    _gate(); move=_doc(MOVE,name,lock=True)
    _stale(move,expected_modified)
    row=next((r for r in move.units if r.tag == tag),None)
    if not row or row.outcome not in ('Pending','Missing'):
        frappe.throw('This unit is not awaiting a check on this movement. Reload the movement.')
    unit=_doc(UNIT,tag,lock=True)
    if unit.active_movement != name or unit.status != 'In transit':
        frappe.throw('This unit is no longer in transit on this movement.')
    received_on=_validate(rules.event_date,received_on,nowdate(),row.received_on or move.dispatched_on)
    note=_validate(rules.receipt,outcome,note,checked_reams,unit.reams_per_bundle if unit.kind=='Bundle' else None)
    if outcome == 'Received':
        _source_hold(unit)
    warehouse=move.source_warehouse if outcome=='Returned' else move.target_warehouse
    _warehouse(warehouse,move.company)
    row.outcome=outcome; row.received_on=received_on; row.note=note
    row.checked_reams=rules.number(checked_reams,'counted reams',integer=True) if outcome=='Received' and unit.kind=='Bundle' else None
    row.checked_by=frappe.session.user; row.checked_at=now_datetime()
    if outcome != 'Missing':
        unit.warehouse=warehouse; unit.status='Available' if outcome=='Received' else 'On hold'
        unit.active_movement=None; unit.last_event_on=received_on
        unit.last_checked_by=frappe.session.user; unit.last_checked_at=now_datetime()
        unit.notes=note; unit.save(ignore_permissions=True)
    pending=any(r.outcome in ('Pending','Missing') for r in move.units)
    move.status='Partly received' if pending else ('Completed' if all(r.outcome=='Received' for r in move.units) else 'Completed with exceptions')
    move.save(ignore_permissions=True)
    return dict(name=name,status=move.status,stock_posted=False)


@frappe.whitelist(methods=['POST'])
def release_hold(name, expected_modified, checked_on, note, checked_reams=None):
    _gate(); unit=_doc(UNIT,name,lock=True); _stale(unit,expected_modified)
    if unit.status != 'On hold' or unit.active_movement or not unit.warehouse:
        frappe.throw('Only a held unit at a known warehouse can be released.')
    _source_hold(unit)
    note=_validate(rules.text,note,'Resolution / inspection note',2000)
    _validate(rules.receipt,'Received',note,checked_reams,unit.reams_per_bundle if unit.kind=='Bundle' else None)
    unit.last_event_on=_validate(rules.event_date,checked_on,nowdate(),unit.last_event_on)
    unit.status='Available'; unit.notes=note; unit.last_checked_by=frappe.session.user;unit.last_checked_at=now_datetime()
    unit.save(ignore_permissions=True)
    return dict(unit=_shape(unit),stock_posted=False)


@frappe.whitelist()
def print_labels(reference=None, name=None):
    _gate()
    if name:
        rows=[_shape(_doc(UNIT,name))]
    else:
        _doc(REG,reference);rows=_registration_units(reference)
    from vcl_procurement.reel_intake import _qr
    for r in rows:
        r['qr_uri']=_qr('/compass/stock-tags/'+quote(r['name'],safe=''))
    if rows and rows[0]['kind']=='Container':
        from vcl_procurement.inventory_labels import container_labels_html
        return dict(html=container_labels_html([dict(r,item_name=r['material']) for r in rows]))
    if rows and rows[0]['kind']=='Reel':
        from vcl_procurement.inventory_labels import reel_labels100_html
        data=[dict(r,batch_id=r['name'],item_name=r['material'],original_qty=r['net_kg'],
            weight_basis='Measured net at registration' if r['weight_known'] else 'Weight not yet measured',
            purchase_order='Existing stock — no purchase order') for r in rows]
        html=reel_labels100_html(dict(reels=data))
        html=html.replace('ORIGINAL QUANTITY','NET AT REGISTRATION').replace('current status and balance','current physical location and status')
        return dict(html='<!doctype html><html><head><title>Existing reel labels</title></head><body>'+html+'</body></html>')
    from vcl_procurement.bundle_print import labels_html
    data=[dict(r,total_sheets=r['reams_per_bundle']*r['sheets_per_ream']) for r in rows]
    html=labels_html(data,'thermal').replace('No transfer recorded','Scan for current physical location').replace('STOCK NOT POSTED','PHYSICAL CUSTODY')
    return dict(html=html)


@frappe.whitelist()
def print_movement(name):
    from vcl_procurement.print_brand import logo_uri
    m=movement_page(name)['movement']
    e=lambda x:escape(str(x or ''),quote=True)
    rows=''.join('<tr><td>'+e(r['tag'])+'</td><td>'+e(r['material'])+'</td><td>'+e(
        str(r['reams_per_bundle'])+' reams × '+str(r['sheets_per_ream'])+' sheets' if r['kind']=='Bundle'
        else (str(r['observed_qty'])+' '+str(r['stock_uom'])+' observed' if r['observed_qty'] is not None else 'Quantity unknown') if r['kind']=='Container'
        else str(r['net_kg'])+' kg at registration' if r['net_kg'] is not None else 'Weight not measured')+
        '</td><td>'+e(r['outcome'])+'</td><td>'+e(r['note'])+'</td></tr>' for r in m['units'])
    return dict(html='''<!doctype html><html><head><title>Physical movement checklist</title><style>
    @page{size:A4;margin:12mm}body{font:12px Arial;color:#111}table{border-collapse:collapse;width:100%}
    td,th{border:1px solid #888;padding:8px;text-align:left;overflow-wrap:anywhere}tr{break-inside:avoid}
    th{background:#eaf0ff;color:#2b3990}h1{color:#2b3990}.vcl-letterhead{display:flex;justify-content:space-between;align-items:center;border-bottom:2px solid #2b3990;padding-bottom:12px;margin:15px 0;color:#2b3990}.vcl-letterhead img{width:240px;height:auto}
    @media print{button{display:none}}h1{font-size:20px}</style></head><body><button onclick="window.print()">Print</button>'''+
    '<header class="vcl-letterhead"><img alt="Vimit Converters Limited" src="'+logo_uri(True)+'"><b>STORES<br>Physical movement checklist</b></header>'+
    '<h1>'+e(m['name'])+'</h1><p>'+e(NOTICE)+'</p><p>From: '+e(m['source_warehouse'])+'<br>To: '+e(m['target_warehouse'])+
    '<br>Dispatch: '+e(m['dispatched_on'])+' · Responsible: '+e(m['custodian'])+'</p><p>'+e(m['notes'])+
    '</p><table><thead><tr><th>Permanent tag</th><th>Material</th><th>Registration quantity</th><th>Outcome</th><th>Notes</th></tr></thead><tbody>'+rows+
    '</tbody></table><p>Dispatched by: '+e(m['dispatched_by'])+'</p><p>Receiver: __________________ Date: __________</p>'+
    '<p>Match every physical label. Record destination checks and exceptions in Compass.</p></body></html>')
