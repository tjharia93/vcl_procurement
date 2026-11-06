"""Validation for preprinted tags. Supplier declarations are never measurements."""
import re
from datetime import date
from vcl_procurement.reels_core import number


def container_key(value):
    return re.sub(r'\s+', '', str(value or '')).upper()


def package_count(description):
    match = re.fullmatch(r'\s*(\d+)\s*REELS?\s*', str(description or ''), re.I)
    return int(match[1]) if match else 0


def clean_preparation(lines, allowed_items):
    if not isinstance(lines, list) or not lines:
        raise ValueError('Add the packing-list reel counts by material.')
    result, seen = [], set()
    for raw in lines:
        item = str(raw.get('item_code') or '')
        count = number(raw.get('count'), 'Reel count', positive=True)
        if item not in allowed_items or item in seen or not count.is_integer() or count > 500:
            raise ValueError('Choose each invoice material once, with a whole reel count (1–500).')
        seen.add(item)
        result.append(dict(item_code=item,count=int(count),width_mm=number(raw.get('width_mm'),'Width',positive=True),
                           gsm=number(raw.get('gsm'),'GSM',positive=True)))
    if sum(r['count'] for r in result) > 500:
        raise ValueError('Prepare at most 500 reels in one delivery.')
    return result


def clean_capture(raw, allowed_items, today):
    fields = {}
    for name in ('width_mm', 'declared_net_kg', 'declared_gross_kg', 'measured_gross_kg', 'core_kg'):
        fields[name] = number(raw.get(name), name.replace('_', ' '))
    if fields['declared_net_kg'] and fields['declared_gross_kg'] and fields['declared_net_kg'] > fields['declared_gross_kg']:
        raise ValueError('Supplier net weight cannot exceed supplier gross weight.')
    if fields['core_kg'] and (not fields['measured_gross_kg'] or fields['core_kg'] >= fields['measured_gross_kg']):
        raise ValueError('Measured core needs a larger measured gross weight.')
    item = str(raw.get('item_code') or '').strip()
    if item and item not in allowed_items:
        raise ValueError('Choose an item from this delivery’s Purchase Order.')
    fields.update(item_code=item or None, supplier_reel_no=str(raw.get('supplier_reel_no') or '').strip()[:140],
                  notes=str(raw.get('notes') or '').strip()[:2000])
    return fields


def arrival_date(value, today):
    try:
        received = date.fromisoformat(str(value))
        current = date.fromisoformat(str(today))
    except (ValueError, TypeError):
        raise ValueError('Enter the actual physical arrival date at VCL.')
    if received > current:
        raise ValueError('Arrival cannot be confirmed for a future date.')
    return received.isoformat()
