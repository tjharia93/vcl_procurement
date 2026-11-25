"""Pure rules for the Scan screen: read a scanned code and describe a unit. No Frappe here.

Labels carry only the ID in their QR (Tanuj, 25/11/2026). Older labels carry a full
link; the last part of that link is the ID, so both kinds scan the same way.
"""
import re
from datetime import date, datetime
from urllib.parse import quote, unquote, urlparse

NOT_FOUND = dict(found=False, message='Not found or not yours to see.')
ID = re.compile(r'^VCL-(RL|BND|IC)-\d{4}-\d{4,6}$')
LOCATION = re.compile(r'^LOC-[A-Z0-9-]{2,40}$')
MAX_LEN = 300


def normalize(code):
    """Return (id, prefix) for a VCL unit ID, ('LOC-..', 'LOC') for a location, or None."""
    text = str(code or '').strip()
    if not text or len(text) > MAX_LEN:
        return None
    if '/' in text:
        path = urlparse(text).path if '://' in text else text.split('?', 1)[0]
        parts = [p for p in path.split('/') if p]
        text = unquote(parts[-1]) if parts else ''
    text = re.sub(r'\s+', '', text).upper()
    match = ID.match(text)
    if match:
        return text, match.group(1)
    if LOCATION.match(text):
        return text, 'LOC'
    return None


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _n(value):
    number = _number(value)
    return f'{number:,.3f}'.rstrip('0').rstrip('.') if number is not None else ''


def _date(value):
    if isinstance(value, (date, datetime)):
        return value.strftime('%d/%m/%Y')
    try:
        return datetime.strptime(str(value or '')[:10], '%Y-%m-%d').strftime('%d/%m/%Y')
    except ValueError:
        return ''


def size_text(width_mm=None, gsm=None, length_mm=None):
    parts = []
    if _number(width_mm) and _number(length_mm):
        parts.append(f'{_n(width_mm)} x {_n(length_mm)} mm')
    elif _number(width_mm):
        parts.append(f'{_n(width_mm)} mm')
    if _number(gsm):
        parts.append(f'{_n(gsm)} GSM')
    return ' | '.join(parts)


def _facts(pairs):
    return [[label, str(value)] for label, value in pairs if value not in (None, '')]


def _result(kind, uid, title, status, facts, url, label):
    return dict(found=True, kind=kind, id=uid, title=title or kind, status=status or '',
                facts=_facts(facts), open=[dict(label=label, url=url)])


def _link(prefix, uid):
    return prefix + quote(uid, safe='')


def reel_intake(d):
    kg = _n(d.get('declared_net_kg'))
    return _result('Reel', d['name'], d.get('material_description'), d.get('status'), [
        ('Item', d.get('item_code')), ('Size', size_text(d.get('width_mm'), d.get('gsm'))),
        ('Weight', f'{kg} kg' if kg else ''), ('Mill reel no.', d.get('supplier_reel_no')),
        ('Warehouse', d.get('warehouse')), ('Received', _date(d.get('received_on')))],
        _link('/compass/reels/', d['name']), 'Open reel record')


def stock_unit(d):
    kg = _n(d.get('net_kg')) if d.get('weight_known') else ''
    qty = _n(d.get('observed_qty') or d.get('nominal_qty'))
    return _result(d.get('kind') or 'Unit', d['name'], d.get('material'), d.get('status'), [
        ('Item', d.get('item_code')),
        ('Size', size_text(d.get('width_mm'), d.get('gsm'), d.get('length_mm'))),
        ('Weight', f'{kg} kg' if kg else ''),
        ('Quantity', f"{qty} {d.get('stock_uom') or ''}".strip() if qty and d.get('kind') == 'Container' else ''),
        ('Expiry', _date(d.get('expiry_date'))), ('Warehouse', d.get('warehouse')),
        ('In transit', 'Yes' if d.get('active_movement') else ''),
        ('Last checked', _date(d.get('last_event_on')))],
        _link('/compass/stock-tags/', d['name']), 'Open stock tag')


def stock_bundle(d):
    reams = d.get('reams_per_bundle')
    return _result('Bundle', d['name'], d.get('material'), d.get('status'), [
        ('Size', size_text(d.get('width_mm'), d.get('gsm'), d.get('length_mm'))),
        ('Reams', reams), ('Sheets', d.get('total_sheets')), ('Warehouse', d.get('intended_warehouse'))],
        _link('/compass/bundles/', d['name']), 'Open bundle record')


def stock_container(d):
    qty = _n(d.get('received_qty') or d.get('nominal_qty'))
    return _result('Container', d['name'], d.get('item_name'), d.get('status'), [
        ('Item', d.get('item_code')), ('Quantity', f"{qty} {d.get('stock_uom') or ''}".strip() if qty else ''),
        ('Expiry', _date(d.get('expiry_date'))), ('Warehouse', d.get('warehouse')),
        ('Received', _date(d.get('received_on')))],
        _link('/compass/containers/', d['name']), 'Open container record')


def batch(d):
    return _result('Reel', d['name'], d.get('item_name') or d.get('item'), '', [
        ('Item', d.get('item')), ('Size', size_text(d.get('custom_reel_width_mm'), d.get('custom_reel_gsm')))],
        _link('/app/batch/', d['name']), 'Open batch')


# Where each prefix is looked for, in order, with the fields read. No price or supplier fields.
SOURCES = {
    'RL': [('Reel Intake', ['name', 'status', 'material_description', 'item_code', 'width_mm', 'gsm',
                            'declared_net_kg', 'supplier_reel_no', 'warehouse', 'received_on'], reel_intake),
           ('Physical Stock Unit', ['name', 'kind', 'material', 'item_code', 'width_mm', 'length_mm', 'gsm', 'net_kg',
                                    'weight_known', 'status', 'warehouse', 'active_movement', 'last_event_on'], stock_unit),
           ('Batch', ['name', 'item', 'item_name', 'custom_reel_width_mm', 'custom_reel_gsm'], batch)],
    'BND': [('Stock Bundle', ['name', 'material', 'width_mm', 'length_mm', 'gsm', 'reams_per_bundle',
                              'total_sheets', 'intended_warehouse', 'status'], stock_bundle),
            ('Physical Stock Unit', ['name', 'kind', 'material', 'width_mm', 'length_mm', 'gsm', 'status',
                                     'warehouse', 'active_movement', 'last_event_on'], stock_unit)],
    'IC': [('Stock Container', ['name', 'item_code', 'item_name', 'nominal_qty', 'received_qty', 'stock_uom', 'status',
                                'expiry_date', 'warehouse', 'received_on'], stock_container),
           ('Physical Stock Unit', ['name', 'kind', 'material', 'item_code', 'nominal_qty', 'observed_qty', 'stock_uom',
                                    'expiry_date', 'status', 'warehouse', 'active_movement', 'last_event_on'], stock_unit)],
}
