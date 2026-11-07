"""Part 1: reserve bundle identities, check physical contents and print labels.

No Item, Batch, stock voucher, stock ledger or valuation is created or changed.
"""
import json
from urllib.parse import quote
import frappe
from frappe import _
from frappe.utils import nowdate, now_datetime
from vcl_procurement.bundle_core import clean_plan, canonical, registration_key, totals, whole, text
from vcl_procurement.reel_intake import _qr

ROLES = {'Stock Manager','Purchase Manager','Purchase User','System Manager'}
NOTICE = 'Bundle identity and count records only. Stock has not been posted. Transfers and valuation are separate.'
FIELDS = ['name','registration','company','material','colour','width_mm','length_mm','gsm',
          'reams_per_bundle','sheets_per_ream','total_sheets','intended_warehouse','status',
          'verified_on','verified_by','checked_reams','notes','modified']


def _gate():
    if not ROLES.intersection(frappe.get_roles()):
        frappe.throw(_('Bundle registration is not available to your role.'),frappe.PermissionError)
    for dt in ('Bundle Registration','Stock Bundle'):
        if not frappe.db.exists('DocType',dt):
            frappe.throw(_('Deploy vcl_procurement and run migrate to install bundle registration.'))


def _company(name):
    company=frappe.get_doc('Company',name)
    company.check_permission('read')
    return company


def _warehouse(name, company):
    warehouse=frappe.get_doc('Warehouse',name)
    warehouse.check_permission('read')
    if warehouse.company != company or warehouse.is_group or warehouse.disabled:
        frappe.throw(_('Choose an active storage warehouse in the selected company.'))


def _registration(name):
    doc=frappe.get_doc('Bundle Registration',name)
    doc.check_permission('read')
    _company(doc.company)
    return doc


def _rows(name):
    return frappe.get_all('Stock Bundle',filters={'registration':name},fields=FIELDS,
                          order_by='name asc',limit_page_length=0)


@frappe.whitelist()
def options():
    _gate()
    return dict(companies=frappe.get_list('Company',pluck='name',limit_page_length=0),
        warehouses=frappe.get_list('Warehouse',filters={'disabled':0,'is_group':0},
            fields=['name','company'],limit_page_length=0),stock_notice=NOTICE)


@frappe.whitelist()
def registrations():
    _gate()
    return frappe.get_list('Bundle Registration',fields=['name','company','material','width_mm',
        'length_mm','gsm','total_bundles','total_reams','total_sheets','intended_warehouse'],
        order_by='creation desc',limit_page_length=100)


@frappe.whitelist()
def registration_page(name):
    _gate()
    doc=_registration(name)
    return dict(name=doc.name,plan=json.loads(doc.plan_json),**totals(json.loads(doc.plan_json)),
                bundles=_rows(doc.name),stock_notice=NOTICE)


@frappe.whitelist(methods=['POST'])
def register(name, plan):
    _gate()
    try:
        name=registration_key(name)
        plan=clean_plan(json.loads(plan) if isinstance(plan,str) else plan)
    except (ValueError,TypeError) as error:
        frappe.throw(str(error))
    _company(plan['company'])
    _warehouse(plan['intended_warehouse'],plan['company'])
    # Company lock serializes first-creation retries, including a race on a new reference.
    frappe.db.sql('select name from `tabCompany` where name=%s for update',(plan['company'],))
    if frappe.db.exists('Bundle Registration',name):
        doc=_registration(name)
        if canonical(json.loads(doc.plan_json)) != canonical(plan):
            frappe.throw(_('This preparation reference already has different details. Open its saved register; do not prepare duplicate labels.'))
        rows=_rows(name)
        if len(rows) != totals(plan)['total_bundles']:
            frappe.throw(_('The saved bundle count is incomplete. Ask a manager to review before printing.'))
        return dict(name=name,reused=True,bundles=rows,stock_posted=False)
    quantities=totals(plan)
    values={key:plan[key] for key in ('company','material','width_mm','length_mm','gsm',
        'sheets_per_ream','reams_per_bundle','intended_warehouse')}
    frappe.get_doc(dict(doctype='Bundle Registration',registration_key=name,plan_json=canonical(plan),
                        **values,**quantities)).insert(ignore_permissions=True)
    from frappe.model.naming import getseries
    prefix='VCL-BND-'+str(nowdate())[:4]+'-'
    rows=[]
    for line in plan['lines']:
        for slot in range(line['count']):
            doc=frappe.get_doc(dict(doctype='Stock Bundle',bundle_id=prefix+getseries(prefix,5),
                registration=name,colour=line['colour'],status='Registered',
                total_sheets=plan['reams_per_bundle']*plan['sheets_per_ream'],**values)).insert(ignore_permissions=True)
            rows.append({key:doc.get(key) for key in FIELDS})
    return dict(name=name,reused=False,bundles=rows,stock_posted=False)


@frappe.whitelist()
def bundle_page(name):
    _gate()
    doc=frappe.get_doc('Stock Bundle',name)
    doc.check_permission('read')
    _company(doc.company)
    _registration(doc.registration)
    return dict(bundle={key:doc.get(key) for key in FIELDS},stock_notice=NOTICE)


@frappe.whitelist(methods=['POST'])
def check_bundle(name, action, checked_reams=None, notes='', expected_modified=None):
    _gate()
    doc=frappe.get_doc('Stock Bundle',name)
    doc.check_permission('read')
    _company(doc.company)
    _registration(doc.registration)
    frappe.db.sql('select name from `tabStock Bundle` where name=%s for update',(name,))
    doc.reload()
    if not expected_modified or str(doc.modified) != str(expected_modified):
        frappe.throw(_('This bundle changed. Reload it before saving your check.'))
    if action not in ('verify','hold'):
        frappe.throw(_('Choose Verify or Put on hold.'))
    try:
        if not isinstance(notes,str) or len(notes)>2000:
            raise ValueError('Keep inspection notes within 2000 characters.')
        if action=='verify':
            count=whole(checked_reams,'Physically counted reams',100000)
            if count != doc.reams_per_bundle:
                raise ValueError('The count differs from the prepared contents. Put the bundle on hold and explain the difference.')
        else:
            text(notes,'Hold reason',2000)
    except ValueError as error:
        frappe.throw(str(error))
    doc.notes=notes.strip()
    doc.status='Checked' if action=='verify' else 'On hold'
    doc.checked_reams=count if action=='verify' else None
    doc.verified_on=now_datetime() if action=='verify' else None
    doc.verified_by=frappe.session.user if action=='verify' else None
    doc.save(ignore_permissions=True)
    return dict(bundle={key:doc.get(key) for key in FIELDS},stock_posted=False)


def _label_data(name, single=False):
    if single:
        row=bundle_page(name)['bundle']
        rows=[row]
    else:
        rows=registration_page(name)['bundles']
    if not rows:
        frappe.throw(_('Register bundles before printing their labels.'))
    for row in rows:
        row['qr_uri']=_qr('/compass/bundles/'+quote(row['name'],safe=''))
    return rows


def bundle_print_html(name, single=False, format='thermal', colour=None):
    from vcl_procurement.bundle_print import labels_html
    rows=_label_data(name,single)
    if colour:
        rows=[r for r in rows if r['colour']==colour]
    return labels_html(rows,format)


def bundle_native_print(name, single=False):
    html=bundle_print_html(name,single)
    # Native print view supplies the outer document; keep only this format's style/pages.
    return html[html.index('<style>'):html.index('</style>')+8]+html[html.index('<article'):html.index('</body>')]


@frappe.whitelist()
def print_labels(name, bundle=None, format='thermal', colour=None):
    _gate()
    if bundle:
        row=bundle_page(bundle)['bundle']
        if row['registration'] != name:
            frappe.throw(_('This bundle does not belong to the selected registration.'))
    if format not in ('a4','thermal'):
        frappe.throw(_('Choose A4 or 100 x 30 mm thermal label stock.'))
    try:
        html=bundle_print_html(bundle or name,bool(bundle),format,colour)
    except ValueError as error:
        frappe.throw(str(error))
    return dict(html=html)
