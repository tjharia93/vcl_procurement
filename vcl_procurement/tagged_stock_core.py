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
    if kind not in ('Reel', 'Bundle'):
        raise ValueError('Choose Reel or Bundle.')
    result = dict(kind=kind, company=text(p.get('company'), 'Company'),
        warehouse=text(p.get('warehouse'), 'Actual source warehouse'),
        material=text(p.get('material'), 'Material description', 80),
        colour=text(p.get('colour', ''), 'Colour', 30, False),
        width_mm=number(p.get('width_mm'), 'width in mm'), gsm=number(p.get('gsm'), 'GSM'),
        observed_on=event_date(p.get('observed_on'), today),
        notes=text(p.get('notes', ''), 'Notes', 2000, False))
    raw = p.get('existing_ids') or []
    if isinstance(raw, str):
        raw = raw.splitlines()
    if not isinstance(raw, list):
        raise ValueError('Enter one existing VCL ID per line.')
    ids = [str(x).strip().upper() for x in raw if str(x).strip()]
    prefix = 'RL' if kind == 'Reel' else 'BND'
    if any(not re.fullmatch(r'VCL-' + prefix + r'-\d{4}-\d{5}', x) for x in ids):
        raise ValueError('Use the full existing VCL ID, with the correct Reel or Bundle prefix.')
    if len(ids) != len(set(ids)):
        raise ValueError('An existing ID is listed more than once.')
    result['existing_ids'] = sorted(ids)
    result['count'] = number(p.get('count'), 'physical unit count', integer=True, maximum=200)
    if ids and len(ids) != result['count']:
        raise ValueError('The count must match the existing IDs. Register untagged units separately.')
    if kind == 'Bundle':
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
