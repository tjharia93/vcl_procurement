"""Existing stock into the books (Phase S): pure rules, no frappe.

A tagged unit's recorded quantity becomes a ledger quantity only through an
explicit, approved unit of measure. Nothing here guesses a weight, a ream count
or a conversion that the item does not state.
"""
import json
import math

from vcl_procurement.tagged_stock_core import text, number, event_date

OUTCOMES = ('Present', 'Not found', 'Held')
TREATMENTS = {'In books': 'Already recorded', 'Missing from books': 'Opening review needed'}
KINDS = ('Opening', 'Transfer')

_KG = {'kg', 'kgs', 'kilogram', 'kilograms'}
_TONNE = {'tonne', 'tonnes', 'ton', 'tons', 'mt', 'metric ton'}
_AREA = {'square meter', 'square metre', 'sq meter', 'sq metre', 'sqm', 'm2', 'm²'}
_REAM = {'ream', 'reams'}
_SHEET = {'sheet', 'sheets'}
_BUNDLE = {'bundle', 'bundles', 'bdl'}
_COUNT = {'nos', 'no', 'unit', 'units', 'pcs', 'piece', 'pieces', 'each'}
_LITRE = {'litre', 'liter', 'litres', 'liters', 'ltr', 'l'}


def _u(uom):
    return str(uom or '').strip().casefold()


def same_measure(a, b):
    a, b = _u(a), _u(b)
    return a == b or (a in _LITRE and b in _LITRE) or (a in _KG and b in _KG)


def uom_fits(kind, item_uom):
    """Raise if this kind of unit can never be expressed in the item's stock UOM."""
    u = _u(item_uom)
    if kind == 'Reel':
        if u in _AREA:
            raise ValueError('This item is stocked in m². m² stock stays a pooled item balance; it is not opened or moved reel by reel.')
        if u not in _KG | _TONNE:
            raise ValueError(f'A reel can only map to an item stocked in Kg or Tonne, not {item_uom}.')
    elif kind == 'Bundle':
        if u not in _REAM | _SHEET | _BUNDLE:
            raise ValueError(f'A bundle can only map to an item stocked in Ream, Sheet or Bundle, not {item_uom}. No kg conversion is assumed.')
    elif kind == 'Container':
        if u not in _KG | _LITRE | _COUNT:
            raise ValueError(f'A container can only map to an item stocked in Kg, Litre or Nos, not {item_uom}.')
    else:
        raise ValueError('Unknown unit kind.')
    return True


def ledger_qty(unit, item_uom):
    """Quantity of one physical unit in the item's stock UOM, or ValueError with the reason."""
    uom_fits(unit['kind'], item_uom)
    u = _u(item_uom)
    name = unit.get('name') or 'This unit'
    if unit['kind'] == 'Reel':
        if not unit.get('weight_known') or not unit.get('net_kg'):
            raise ValueError(f'{name} has no measured weight. Weigh it, or leave it out of the draft.')
        kg = float(unit['net_kg'])
        return round(kg / 1000, 6) if u in _TONNE else kg
    if unit['kind'] == 'Bundle':
        reams, sheets = unit.get('reams_per_bundle'), unit.get('sheets_per_ream')
        if u in _BUNDLE:
            return 1
        if not reams:
            raise ValueError(f'{name} has no reams-per-bundle recorded.')
        if u in _REAM:
            return int(reams)
        if not sheets:
            raise ValueError(f'{name} has no sheets-per-ream recorded.')
        return int(reams) * int(sheets)
    # Container
    if u in _COUNT:
        if unit.get('fill_state') != 'Full':
            raise ValueError(f'{name} is not a full container. An item counted in Nos can only take full containers.')
        return 1
    if not same_measure(unit.get('stock_uom'), item_uom):
        raise ValueError(f'{name} is measured in {unit.get("stock_uom")}, the item in {item_uom}. No conversion is assumed.')
    if not unit.get('quantity_known') or not unit.get('observed_qty'):
        raise ValueError(f'{name} has no known quantity.')
    return float(unit['observed_qty'])


def _rows(value, label):
    rows = json.loads(value) if isinstance(value, str) else value
    if not isinstance(rows, list) or not 1 <= len(rows) <= 200:
        raise ValueError(f'Select between 1 and 200 {label}.')
    names = [text(r.get('name') if isinstance(r, dict) else None, 'Tag ID') for r in rows]
    if len(set(names)) != len(names):
        raise ValueError('Each tag can appear only once.')
    if any(not r.get('modified') for r in rows):
        raise ValueError('Reload the units before saving.')
    return rows


def units(value):
    rows = _rows(value, 'units')
    return sorted(({'name': r['name'].strip(), 'modified': str(r['modified'])} for r in rows), key=lambda r: r['name'])


def sheet_rows(value):
    out = []
    for r in _rows(value, 'sheet rows'):
        outcome = r.get('outcome')
        if outcome not in OUTCOMES:
            raise ValueError('Each row needs Present, Not found or Held, as ticked on the signed sheet.')
        note = text(r.get('note') or '', 'Note', 2000, outcome != 'Present')
        out.append({'name': r['name'].strip(), 'modified': str(r['modified']), 'outcome': outcome, 'note': note})
    return sorted(out, key=lambda r: r['name'])


def treatment(value):
    if value not in TREATMENTS:
        raise ValueError('Choose In books or Missing from books.')
    return TREATMENTS[value]


def draft_request(kind, posting_date, today, rates=None, source_warehouse=None):
    if kind not in KINDS:
        raise ValueError('Choose an opening draft or a transfer draft.')
    result = {'kind': kind, 'posting_date': event_date(posting_date, today)}
    if kind == 'Opening':
        raw = json.loads(rates) if isinstance(rates, str) else (rates or {})
        if not isinstance(raw, dict) or not raw:
            raise ValueError('Enter the approved valuation rate for each item.')
        clean = {}
        for item, rate in raw.items():
            clean[text(item, 'Item')] = number(rate, f'valuation rate for {item}', maximum=1e9)
        result['rates'] = clean
    else:
        result['source_warehouse'] = text(source_warehouse, 'Warehouse where the books hold the stock')
    return result


def voucher_lines(units_with_qty, batched):
    """Group units into voucher lines.

    units_with_qty: [{name, item_code, warehouse, qty}]; batched: set of batch-tracked items.
    Unbatched lines are summed per item and warehouse. A batched item gets one line per unit,
    its batch being the unit's permanent ID.
    """
    lines, index = [], {}
    for u in sorted(units_with_qty, key=lambda x: x['name']):
        if u['item_code'] in batched:
            lines.append({'item_code': u['item_code'], 'warehouse': u['warehouse'], 'qty': u['qty'],
                          'batch_no': u['name'], 'tags': [u['name']]})
            continue
        key = (u['item_code'], u['warehouse'])
        if key not in index:
            index[key] = {'item_code': u['item_code'], 'warehouse': u['warehouse'], 'qty': 0, 'batch_no': None, 'tags': []}
            lines.append(index[key])
        index[key]['qty'] = round(index[key]['qty'] + u['qty'], 6)
        index[key]['tags'].append(u['name'])
    for line in lines:
        if not math.isfinite(line['qty']) or line['qty'] <= 0:
            raise ValueError('A voucher line has no positive quantity.')
    return lines


def draft_blockers(unit, kind):
    """Reasons a unit cannot go into this kind of draft (empty list = eligible)."""
    name = unit.get('name')
    out = []
    if unit.get('count_outcome') != 'Present':
        out.append(f'{name} is not confirmed present on a signed tagging sheet.')
    if unit.get('status') != 'Available' or unit.get('active_movement'):
        out.append(f'{name} is on hold or in transit.')
    if not unit.get('item_code'):
        out.append(f'{name} has no item code. Tanuj sets it first.')
    if not unit.get('warehouse'):
        out.append(f'{name} has no physical warehouse.')
    if unit.get('stock_draft') and unit.get('stock_draft_status') in ('Draft', 'Submitted'):
        out.append(f'{name} is already on {unit.get("stock_draft")} ({unit.get("stock_draft_status")}).')
    want = 'Opening review needed' if kind == 'Opening' else 'Already recorded'
    if unit.get('ledger_status') != want:
        out.append(f'{name} is not marked {"Missing from books" if kind == "Opening" else "In books"}.')
    return out
