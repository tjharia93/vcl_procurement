"""Existing stock into the books (Phase S): item links, signed tagging sheets, draft vouchers.

Decisions behind this module (Tanuj, 16 November 2026):
- Existing stock is confirmed paper-first: the office prints a tagging sheet per
  registration, the floor tags the units and signs the sheet, and the office records
  the signed sheet here. No phone count is needed for this first drive.
- Tanuj assigns the item code. Other users can read it, not set it.
- Pages create drafts only. A reviewer submits the voucher in ERPNext.

Every write takes role + company permission, row locks and the unit's `modified`
version. Every request carries a caller-made ID; repeating it returns the first result.
"""
from html import escape

import frappe
from frappe.utils import nowdate, now_datetime

from vcl_procurement import existing_stock_core as rules
from vcl_procurement import tagged_stock_core as tag_rules
from vcl_procurement.tagged_stock import UNIT, REG, _gate, _doc, _company, _warehouse, _stale, _shape, _validate

SHEET = 'Tagging Sheet Return'
DRAFT = 'Existing Stock Draft'
NOTICE = 'Drafts only. Nothing here submits a stock voucher; a reviewer submits it in ERPNext.'
DEFAULT_APPROVERS = ('tanuj.haria@vimit.com',)


def _approvers():
    return set(frappe.conf.get('tagged_stock_item_approvers') or DEFAULT_APPROVERS) | {'Administrator'}


def _approver():
    if frappe.session.user not in _approvers():
        frappe.throw('Only Tanuj sets item codes and prepares stock drafts for existing stock.', frappe.PermissionError)


def _item(item_code):
    if not item_code or not frappe.db.exists('Item', item_code):
        frappe.throw('Choose an existing stock item.')
    item = frappe.get_doc('Item', item_code)
    item.check_permission('read')
    if item.disabled or not item.is_stock_item:
        frappe.throw(f'{item_code} is disabled or not a stock item.')
    return item


def _unit_dict(doc):
    data = _shape(doc)
    data.update({k: doc.get(k) for k in ('count_outcome', 'counted_on', 'count_signed_by', 'count_sheet',
        'stock_draft_doctype', 'stock_draft', 'stock_draft_status', 'item_set_by', 'item_set_at')})
    # _shape hides an unknown quantity; ledger_qty needs to see the flags too.
    data.update(weight_known=doc.get('weight_known'), quantity_known=doc.get('quantity_known'))
    if doc.get('weight_known'):
        data['net_kg'] = doc.get('net_kg')
    if doc.get('quantity_known'):
        data['observed_qty'] = doc.get('observed_qty')
    return data


@frappe.whitelist()
def permissions():
    """What the signed-in user may do on the existing-stock page. Read only."""
    _gate()
    return dict(can_set_item=frappe.session.user in _approvers(), can_record_sheet=True, notice=NOTICE)


# ---------------------------------------------------------------- item link

@frappe.whitelist(methods=['POST'])
def set_item(units, item_code, treatment):
    """Tanuj links units to the booked item and says whether the books already hold them."""
    _gate(); _approver()
    rows = _validate(rules.units, units)
    ledger_status = _validate(rules.treatment, treatment)
    item = _item(item_code)
    done = []
    for row in rows:
        unit = _doc(UNIT, row['name'], lock=True)
        _stale(unit, row['modified'])
        if unit.stock_draft and unit.stock_draft_status in ('Draft', 'Submitted'):
            frappe.throw(f'{unit.name} is already on {unit.stock_draft}. Cancel or delete that draft before changing its item.')
        _validate(rules.uom_fits, unit.kind, item.stock_uom)
        if unit.kind == 'Container' and unit.item_code and unit.item_code != item.name and unit.source_doctype:
            frappe.throw(f'{unit.name} is a purchased container; its item comes from its delivery record.')
        unit.item_code = item.name
        if unit.kind != 'Container':
            # A container keeps the unit its remaining quantity was measured in.
            unit.stock_uom = item.stock_uom
        unit.ledger_status = ledger_status
        unit.item_set_by = frappe.session.user
        unit.item_set_at = now_datetime()
        unit.save(ignore_permissions=True)
        done.append(_unit_dict(unit))
    return dict(units=done, stock_posted=False)


# ---------------------------------------------------------------- tagging sheet

def _sheet_regs(key):
    """Registrations on one tagging sheet, oldest first. Refuses an unknown or mixed-company sheet."""
    if key and frappe.db.exists(REG, key):
        names = [key]
    else:
        names = [r.name for r in frappe.get_all(REG, filters={'name': ['like', str(key) + '-%']}, fields=['name'], limit_page_length=0)
                 if rules.sheet_key(r.name) == key]
    if not key or not names:
        frappe.throw('This tagging sheet was not found. Reload the page.')
    regs = sorted((_doc(REG, n) for n in names), key=lambda r: (str(r.observed_on), r.name))
    if len({r.company for r in regs}) > 1:
        frappe.throw('This sheet mixes companies. Review its registrations.')
    return regs


def _sheet_units(regs):
    names = [r.name for r in regs]
    return [_unit_dict(frappe.get_doc(UNIT, r.name)) for r in
            frappe.get_all(UNIT, filters={'registration': ['in', names]}, fields=['name'], order_by='name asc', limit_page_length=0)]


def _spec(u):
    if u['kind'] == 'Reel':
        bits = [f"{u['width_mm']:g} mm" if u.get('width_mm') else '', f"{u['gsm']:g} gsm" if u.get('gsm') else '',
                f"{u['net_kg']:g} kg" if u.get('net_kg') is not None else 'not weighed']
    elif u['kind'] == 'Bundle':
        bits = [f"{u['width_mm']:g} × {u['length_mm']:g} mm" if u.get('width_mm') and u.get('length_mm') else '',
                f"{u['gsm']:g} gsm" if u.get('gsm') else '',
                f"{u.get('reams_per_bundle')} reams × {u.get('sheets_per_ream')} sheets"]
    else:
        bits = [f"{u.get('nominal_qty') or ''} {u.get('stock_uom') or ''} pack",
                f"{u['observed_qty']:g} {u.get('stock_uom')} left" if u.get('observed_qty') is not None else 'quantity unknown']
    return ' · '.join(b for b in bits if b)


@frappe.whitelist()
def tagging_sheet(reference):
    """Printable A4 sheet: every unit of one registration or bulk batch, with tick boxes and a signature line."""
    _gate()
    regs = _sheet_regs(reference)
    units = _sheet_units(regs)
    stores = sorted({u.get('warehouse') or '' for u in units} - {''}) or [regs[0].warehouse]
    dates = sorted({str(r.observed_on) for r in regs})
    from vcl_procurement.print_brand import logo_uri
    e = lambda x: escape(str(x if x is not None else ''), quote=True)
    rows = ''.join(
        '<tr><td class="id">' + e(u['name']) + '</td><td>' + e(u['material']) + (' · ' + e(u['colour']) if u.get('colour') else '') +
        '<br><small>' + e(_spec(u)) + '</small></td><td>' + e(u.get('item_code') or '—') + '</td><td>' + e(u.get('warehouse') or '') +
        '</td><td class="box"></td><td class="box"></td><td class="box"></td><td class="note"></td></tr>'
        for u in units)
    html = ('<!doctype html><html><head><meta charset="utf-8"><title>Tagging sheet ' + e(reference) + '</title><style>'
        '@page{size:A4;margin:12mm}body{font:12px Arial;color:#111}table{border-collapse:collapse;width:100%}'
        'td,th{border:1px solid #888;padding:7px;text-align:left;vertical-align:top;overflow-wrap:anywhere}tr{break-inside:avoid}'
        'th{background:#eaf0ff;color:#2b3990}.id{font-family:monospace;white-space:nowrap}.box{width:46px}.note{width:22%}'
        '.vcl-letterhead{display:flex;justify-content:space-between;align-items:center;border-bottom:2px solid #2b3990;padding-bottom:10px;margin:12px 0;color:#2b3990}'
        '.vcl-letterhead img{width:220px;height:auto}.sign{display:flex;gap:40px;margin-top:28px}.sign div{flex:1;border-top:1px solid #111;padding-top:4px}'
        '@media print{button{display:none}}</style></head><body><button onclick="window.print()">Print</button>'
        '<header class="vcl-letterhead"><img alt="Vimit Converters Limited" src="' + logo_uri(True) + '"><b>STORES<br>Existing stock tagging sheet</b></header>'
        '<p><b>Store:</b> ' + e(', '.join(stores)) + ' · <b>Registered:</b> ' + e(' to '.join([dates[0], dates[-1]] if len(dates) > 1 else dates)) +
        ' · <b>Units:</b> ' + e(len(units)) + '<br><small>Sheet ' + e(reference) + '</small></p>'
        '<p>Stick each label on its unit. Tick one box per row. A unit not found or held needs a note. Sign and return this sheet to the office.</p>'
        '<table><thead><tr><th>Tag</th><th>Material</th><th>Item code</th><th>Store</th><th>Tagged, present</th><th>Not found</th><th>Held</th><th>Note</th></tr></thead><tbody>'
        + rows + '</tbody></table><div class="sign"><div>Tagged by (name)</div><div>Signature</div><div>Date</div></div>'
        '<p><small>' + e(NOTICE) + '</small></p></body></html>')
    return dict(html=html)


@frappe.whitelist(methods=['POST'])
def record_sheet(sheet_id, reference, signed_by, signed_on, rows):
    """Record the signed tagging sheet exactly as ticked."""
    _gate()
    sheet_id = _validate(tag_rules.text, sheet_id, 'Sheet record ID')
    signed_by = _validate(tag_rules.text, signed_by, 'Name of the person who signed')
    regs = _sheet_regs(reference)
    reg_names = {r.name for r in regs}
    signed_on = _validate(tag_rules.event_date, signed_on, nowdate(), max(str(r.observed_on) for r in regs))
    sheet = _validate(rules.sheet_rows, rows)
    request = tag_rules.canonical(dict(reference=reference, signed_by=signed_by, signed_on=signed_on,
        rows=[{k: r[k] for k in ('name', 'outcome', 'note')} for r in sheet]))
    for r in regs:
        frappe.db.sql('select name from `tabStock Tag Registration` where name=%s for update', (r.name,))
    if frappe.db.exists(SHEET, sheet_id):
        saved = _doc(SHEET, sheet_id)
        if saved.request_json != request:
            frappe.throw('This sheet record already holds different ticks. Open it rather than recording again.')
        return dict(name=sheet_id, reused=True, units=_sheet_units(regs), stock_posted=False)
    units = []
    for row in sheet:
        unit = _doc(UNIT, row['name'], lock=True)
        _stale(unit, row['modified'])
        if unit.registration not in reg_names:
            frappe.throw(f'{unit.name} is not on sheet {reference}.')
        if unit.status == 'In transit' or unit.active_movement:
            frappe.throw(f'{unit.name} is in transit. Receive it before recording this sheet.')
        units.append((unit, row))
    frappe.get_doc(dict(doctype=SHEET, sheet_id=sheet_id, registration=regs[0].name, company=regs[0].company,
        signed_by=signed_by, signed_on=signed_on, recorded_by=frappe.session.user,
        recorded_at=now_datetime(), request_json=request)).insert(ignore_permissions=True)
    for unit, row in units:
        unit.count_outcome = row['outcome']
        unit.counted_on = signed_on
        unit.count_signed_by = signed_by
        unit.count_sheet = sheet_id
        if row['outcome'] == 'Held':
            unit.status = 'On hold'
        if row['note']:
            unit.notes = row['note']
        unit.last_event_on = max(str(unit.last_event_on or signed_on), signed_on)
        unit.last_checked_by = frappe.session.user
        unit.last_checked_at = now_datetime()
        unit.save(ignore_permissions=True)
    return dict(name=sheet_id, reused=False, units=_sheet_units(regs), stock_posted=False)


# ---------------------------------------------------------------- drafts

def _balance(item_code, warehouse, posting_date):
    from erpnext.stock.utils import get_stock_balance
    return float(get_stock_balance(item_code, warehouse, posting_date) or 0)


def _batch(tag, item_code):
    """The unit's permanent ID is its batch. Reuse an empty batch of the same item; never another item's."""
    if frappe.db.exists('Batch', tag):
        batch = frappe.get_doc('Batch', tag)
        if batch.item != item_code:
            frappe.throw(f'Batch {tag} belongs to {batch.item}. Review it before opening this unit.')
        from erpnext.stock.doctype.batch.batch import get_batch_qty
        if float(get_batch_qty(batch_no=tag, item_code=item_code) or 0):
            frappe.throw(f'Batch {tag} already holds stock. It is in the books; use a transfer, not an opening.')
        return tag
    frappe.get_doc(dict(doctype='Batch', batch_id=tag, item=item_code)).insert(ignore_permissions=True)
    return tag


@frappe.whitelist()
def draft_preview(kind, units, posting_date, rates=None, source_warehouse=None):
    """What a draft would contain, and what blocks it. Read only."""
    _gate()
    plan = _plan(kind, units, posting_date, rates, source_warehouse, lock=False)
    plan.pop('docs')
    return plan


def _plan(kind, units, posting_date, rates, source_warehouse, lock):
    req = _validate(rules.draft_request, kind, posting_date, nowdate(), rates, source_warehouse)
    rows = _validate(rules.units, units)
    blockers, qty_rows, docs, items, company = [], [], [], {}, None
    for row in rows:
        unit = _doc(UNIT, row['name'], lock=lock)
        if lock:
            _stale(unit, row['modified'])
        data = _unit_dict(unit)
        company = company or unit.company
        if unit.company != company:
            blockers.append(f'{unit.name} belongs to another company.')
        blockers += rules.draft_blockers(data, kind)
        if unit.item_code:
            items.setdefault(unit.item_code, _item(unit.item_code))
            try:
                qty = rules.ledger_qty(data, items[unit.item_code].stock_uom)
                qty_rows.append(dict(name=unit.name, item_code=unit.item_code, warehouse=unit.warehouse, qty=qty))
            except ValueError as exc:
                blockers.append(str(exc))
        docs.append(unit)
    batched = {code for code, item in items.items() if item.has_batch_no}
    lines = []
    if not blockers:
        try:
            lines = rules.voucher_lines(qty_rows, batched)
        except ValueError as exc:
            blockers.append(str(exc))
    if kind == 'Opening':
        for code in items:
            if code not in req['rates']:
                blockers.append(f'Enter the approved valuation rate for {code}.')
        for line in lines:
            if not line['batch_no'] and _balance(line['item_code'], line['warehouse'], req['posting_date']):
                blockers.append(f'{line["warehouse"]} already holds {line["item_code"]} in the books. An opening would overwrite it; use a transfer.')
    else:
        _warehouse(req['source_warehouse'], company)
        for code in batched:
            blockers.append(f'{code} is batch-tracked. Choose its batches in ERPNext Desk; this page drafts unbatched transfers only.')
        need = {}
        for line in lines:
            if line['warehouse'] == req['source_warehouse']:
                blockers.append(f'{line["item_code"]} is already booked where it physically is. No transfer is needed.')
            need[line['item_code']] = need.get(line['item_code'], 0) + line['qty']
        for code, qty in need.items():
            have = _balance(code, req['source_warehouse'], req['posting_date'])
            if have < qty:
                blockers.append(f'{req["source_warehouse"]} holds {have:g} {items[code].stock_uom} of {code}; the units need {qty:g}.')
    for line in lines:
        line['stock_uom'] = items[line['item_code']].stock_uom
        if kind == 'Opening':
            line['valuation_rate'] = req['rates'].get(line['item_code'])
    return dict(request=req, company=company, lines=lines, blockers=sorted(set(blockers)),
                units=[u.name for u in docs], notice=NOTICE, docs=docs)


@frappe.whitelist(methods=['POST'])
def prepare_draft(request_id, kind, units, posting_date, rates=None, source_warehouse=None):
    """Create one draft Stock Reconciliation (opening) or Stock Entry (transfer). Never submits."""
    _gate(); _approver()
    request_id = _validate(tag_rules.text, request_id, 'Draft request ID')
    plan = _plan(kind, units, posting_date, rates, source_warehouse, lock=False)
    request = tag_rules.canonical(dict(kind=kind, request=plan['request'], units=plan['units']))
    if plan['company']:
        _company(plan['company'])
        frappe.db.sql('select name from `tabCompany` where name=%s for update', (plan['company'],))
    if frappe.db.exists(DRAFT, request_id):
        saved = _doc(DRAFT, request_id)
        if saved.request_json != request:
            frappe.throw('This draft request ID was used for different units or values.')
        return dict(name=request_id, voucher_doctype=saved.voucher_doctype, voucher_name=saved.voucher_name,
                    reused=True, stock_posted=False)
    plan = _plan(kind, units, posting_date, rates, source_warehouse, lock=True)
    if plan['blockers']:
        frappe.throw('<br>'.join(escape(b) for b in plan['blockers']))
    req = plan['request']
    if kind == 'Opening':
        items = []
        for line in plan['lines']:
            row = dict(item_code=line['item_code'], warehouse=line['warehouse'], qty=line['qty'],
                       valuation_rate=line['valuation_rate'])
            if line['batch_no']:
                row.update(use_serial_batch_fields=1, batch_no=_batch(line['batch_no'], line['item_code']))
            items.append(row)
        from erpnext.stock.doctype.stock_reconciliation.stock_reconciliation import get_difference_account
        opening_account = get_difference_account('Opening Stock', plan['company'])
        if not opening_account:
            frappe.throw('The company has no Temporary Opening account for opening stock.')
        voucher = frappe.get_doc(dict(doctype='Stock Reconciliation', company=plan['company'], purpose='Opening Stock',
            posting_date=req['posting_date'], posting_time='00:00:00', set_posting_time=1,
            expense_account=opening_account, cost_center=frappe.get_cached_value('Company', plan['company'], 'cost_center'),
            items=items))
    else:
        voucher = frappe.get_doc(dict(doctype='Stock Entry', company=plan['company'], stock_entry_type='Material Transfer',
            purpose='Material Transfer', posting_date=req['posting_date'], set_posting_time=1,
            remarks='Existing tagged stock moved in the books to where it physically is: ' + ', '.join(plan['units']),
            items=[dict(item_code=l['item_code'], qty=l['qty'], uom=l['stock_uom'], stock_uom=l['stock_uom'],
                        conversion_factor=1, s_warehouse=req['source_warehouse'], t_warehouse=l['warehouse'])
                   for l in plan['lines']]))
    voucher.insert()
    frappe.get_doc(dict(doctype=DRAFT, request_id=request_id, company=plan['company'], kind=kind,
        voucher_doctype=voucher.doctype, voucher_name=voucher.name, prepared_by=frappe.session.user,
        request_json=request)).insert(ignore_permissions=True)
    for unit in plan['docs']:
        unit.stock_draft_doctype = voucher.doctype
        unit.stock_draft = voucher.name
        unit.stock_draft_status = 'Draft'
        unit.save(ignore_permissions=True)
    return dict(name=request_id, voucher_doctype=voucher.doctype, voucher_name=voucher.name,
                reused=False, stock_posted=False)


# ---------------------------------------------------------------- voucher lifecycle

def on_voucher_change(doc, method=None):
    """doc_events for Stock Reconciliation and Stock Entry: keep linked units' posting state true."""
    if not frappe.db.has_column(UNIT, 'stock_draft'):
        return
    names = frappe.get_all(UNIT, filters={'stock_draft_doctype': doc.doctype, 'stock_draft': doc.name}, pluck='name')
    if not names:
        return
    status = {'on_submit': 'Submitted', 'on_cancel': 'Cancelled', 'on_trash': ''}.get(method)
    if status is None:
        return
    for name in names:
        values = dict(stock_draft_status=status)
        if method == 'on_trash':
            values.update(stock_draft_doctype=None, stock_draft=None)
        frappe.db.set_value(UNIT, name, values, update_modified=True)
