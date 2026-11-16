"""Propose explicit PO quantity increases; never change a stock allowance."""
import math


def number(value, label):
    if isinstance(value, bool):
        raise ValueError(f'{label} must be a number.')
    try:
        value = float(value)
    except (ValueError, TypeError):
        raise ValueError(f'{label} must be a number.')
    if not math.isfinite(value) or value < 0:
        raise ValueError(f'{label} must be a finite, non-negative number.')
    return value


def requested_lines(value, allowed):
    if not isinstance(value, dict) or not value:
        raise ValueError('Enter the actual quantities to receive.')
    result = {}
    for key, raw in value.items():
        key = str(key)
        if key not in allowed:
            raise ValueError('A receiving line does not belong to this Purchase Order.')
        qty = number(raw, 'Receiving quantity')
        if qty <= 0:
            raise ValueError('Each selected receiving quantity must be positive.')
        result[key] = qty
    return result


def proposal(rows, requested, limits):
    by_idx = {str(r['idx']): r for r in rows}
    wanted = requested_lines(requested, by_idx)
    changes = []
    for idx, qty in wanted.items():
        row = by_idx[idx]
        ceiling = number(limits[int(idx)][1], 'Receipt limit')
        if qty <= ceiling + 1e-9:
            continue
        ordered = number(row['qty'], 'Ordered quantity')
        received = number(row.get('received_qty') or 0, 'Received quantity')
        target = round(number(received + qty, 'New order quantity'), 6)
        extra = round(target - ordered, 6)
        if extra <= 0:
            raise ValueError('The receiving limits changed. Reload the Purchase Order.')
        rate = number(row['rate'], 'Existing PO rate')
        changes.append(dict(idx=int(idx), po_detail=row['name'], item_code=row['item_code'],
            item_name=row.get('item_name') or row['item_code'], uom=row['uom'],
            ordered=ordered, received=received, receive_now=qty, max_qty=ceiling,
            new_order_qty=target, extra_qty=extra, rate=rate, extra_net=round(number(extra*rate, 'Additional value'), 6)))
    return wanted, sorted(changes, key=lambda r:r['idx'])


def native_items(rows, changes):
    """ERPNext deletes omitted rows: always retain every original child identity."""
    targets = {r['po_detail']: r['new_order_qty'] for r in changes}
    return [dict(docname=r['name'], idx=r['idx'], item_code=r['item_code'],
        qty=targets.get(r['name'], r['qty']), rate=r['rate'], uom=r['uom'],
        conversion_factor=r.get('conversion_factor') or 1, schedule_date=str(r.get('schedule_date') or ''),
        description=r.get('description')) for r in rows]
