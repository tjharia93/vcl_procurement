"""Physical custody rules. Quantities here are observations, never ledger balances."""
import json
import math
import re
from datetime import date


def text(value, label, limit=140, required=True):
    if not isinstance(value, str) or len(value.strip()) > limit or (required and not value.strip()):
        raise ValueError(f'{label} is required and must fit within {limit} characters.')
    return value.strip()


def number(value, label, optional=False, integer=False, maximum=1000000):
    if optional and value in (None, ''):
        return None
    try:
        n = float(str(value).replace(',', '').replace(' ', ''))
    except (ValueError, TypeError):
        raise ValueError(f'Enter a positive {label}.')
    if not math.isfinite(n) or n <= 0 or n > maximum or (integer and n != int(n)):
        raise ValueError(f'Enter a positive {label}' + (' in whole numbers.' if integer else '.'))
    return int(n) if integer else n


def event_date(value, today, earliest=None):
    try:
        result = date.fromisoformat(value)
        if result > date.fromisoformat(str(today)[:10]) or (earliest and result < date.fromisoformat(str(earliest)[:10])):
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('Choose a date no earlier than the previous physical event and no later than today.')
    return result.isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def plan(value, today):
    p = json.loads(value) if isinstance(value, str) else value
    if not isinstance(p, dict):
        raise ValueError('Enter the registration details.')
    kind = p.get('kind')
    if kind not in ('Reel', 'Bundle', 'Container'):
        raise ValueError('Choose Reel, Bundle or Ink / chemical container.')
    result = dict(kind=kind, company=text(p.get('company'), 'Company'),
        warehouse=text(p.get('warehouse'), 'Actual source warehouse'),
        material=text(p.get('material'), 'Material description', 140 if kind=='Container' else 80),
        colour=text(p.get('colour', ''), 'Colour', 30, False),
        width_mm=number(p.get('width_mm'), 'width in mm', optional=kind=='Container'), gsm=number(p.get('gsm'), 'GSM', optional=kind=='Container'),
        observed_on=event_date(p.get('observed_on'), today),
        notes=text(p.get('notes', ''), 'Notes', 2000, False))
    raw = p.get('existing_ids') or []
    if isinstance(raw, str):
        raw = raw.splitlines()
    if not isinstance(raw, list):
        raise ValueError('Enter one existing VCL ID per line.')
    ids = [str(x).strip().upper() for x in raw if str(x).strip()]
    prefix = {'Reel':'RL','Bundle':'BND','Container':'IC'}[kind]
    if any(not re.fullmatch(r'VCL-' + prefix + r'-\d{4}-\d{5}', x) for x in ids):
        raise ValueError('Use the full existing VCL ID, with the correct Reel or Bundle prefix.')
    if len(ids) != len(set(ids)):
        raise ValueError('An existing ID is listed more than once.')
    result['existing_ids'] = sorted(ids)
    result['count'] = number(p.get('count'), 'physical unit count', integer=True, maximum=200)
    if ids and len(ids) != result['count']:
        raise ValueError('The count must match the existing IDs. Register untagged units separately.')
    if kind == 'Container':
        unit = p.get('stock_uom')
        if unit not in ('Kg','Litre','Liter'):
            raise ValueError('Choose an ink/chemical item stocked in Kg or Litre. Do not convert mass into volume.')
        state = p.get('fill_state')
        if state not in ('Full','Part used','Unknown'):
            raise ValueError('Choose Full, Part used or Quantity unknown.')
        nominal = number(p.get('nominal_qty'),'nominal quantity per container')
        actual = nominal if state=='Full' else number(p.get('observed_qty'),'actual remaining quantity',optional=state=='Unknown')
        if state=='Unknown':actual=None
        if actual is not None and actual>nominal:
            raise ValueError('Remaining quantity cannot exceed the nominal pack size.')
        if state=='Part used' and (result['count']!=1 or actual>=nominal):
            raise ValueError('Register partly used cans individually, with remaining quantity below the nominal pack size.')
        expiry=text(p.get('expiry_date',''),'Expiry date',10,False)
        if expiry:date.fromisoformat(expiry)
        ledger=p.get('ledger_status')
        if ledger not in ('Already recorded','Opening review needed','Not sure'):
            raise ValueError('Record whether this stock is already in ERPNext or needs opening review.')
        result.update(item_code=text(p.get('item_code'),'Material item'),stock_uom=unit,nominal_qty=nominal,
            observed_qty=actual,quantity_known=actual is not None,fill_state=state,
            supplier_name=text(p.get('supplier_name',''),'Supplier name',140,False),
            supplier_lot=text(p.get('supplier_lot',''),'Supplier lot',140,False),expiry_date=expiry,
            ledger_status=ledger)
    elif kind == 'Bundle':
        result.update(length_mm=number(p.get('length_mm'), 'length in mm'),
            sheets_per_ream=number(p.get('sheets_per_ream'), 'sheets per ream', integer=True),
            reams_per_bundle=number(p.get('reams_per_bundle'), 'reams per bundle', integer=True))
    else:
        result['net_kg'] = number(p.get('net_kg'), 'measured net kg', optional=True)
        if result['net_kg'] is not None and result['count'] != 1:
            raise ValueError('Register weighed reels individually. Never copy one weight to several reels.')
    return result


def selected(value):
    rows = json.loads(value) if isinstance(value, str) else value
    if not isinstance(rows, list) or not 1 <= len(rows) <= 200:
        raise ValueError('Select between 1 and 200 tagged units.')
    names = [text(x.get('name'), 'Tag ID') for x in rows]
    if len(set(names)) != len(names):
        raise ValueError('Select each tag only once.')
    if any(not x.get('modified') for x in rows):
        raise ValueError('Reload the tag list before dispatching.')
    return sorted(rows, key=lambda x: x['name'])


def receipt(action, note, checked_reams, expected_reams):
    if action not in ('Received', 'On hold', 'Missing', 'Returned'):
        raise ValueError('Choose Received, On hold, Missing or Returned.')
    note = text(note, 'Exception / inspection note', 2000, action != 'Received')
    if action == 'Received' and expected_reams:
        if number(checked_reams, 'counted reams', integer=True) != expected_reams:
            raise ValueError('Ream count differs. Receive this bundle On hold and describe the count.')
    return note
