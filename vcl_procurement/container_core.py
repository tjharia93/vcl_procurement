"""Physical ink/chemical identity rules. These values are not stock balances."""
import hashlib
import math
import datetime
import json


def positive(value, label):
    if isinstance(value, bool):
        raise ValueError(f'{label} must be a positive number.')
    try:
        number = float(str(value).replace(',', '').replace(' ', ''))
    except (ValueError, TypeError):
        raise ValueError(f'{label} must be a positive number.')
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f'{label} must be a positive number.')
    return number


def preparation(count, pack_qty, stock_uom, ordered_stock_qty):
    count = positive(count, 'Container count')
    if int(count) != count or count > 1000:
        raise ValueError('Prepare 1 to 1000 whole containers per PO line.')
    if stock_uom not in ('Kg', 'Litre', 'Liter'):
        raise ValueError('Container tracking supports items stocked in Kg or Litre. Review the item units first.')
    pack_qty = positive(pack_qty, 'Nominal quantity per container')
    if count * pack_qty > positive(ordered_stock_qty, 'Ordered stock quantity') + 0.000001:
        raise ValueError('The nominal container total exceeds this PO line. Review the order before preparing extra labels.')
    return dict(count=int(count), pack_qty=pack_qty, stock_uom=stock_uom)


def allocation_key(po, po_detail):
    # A linked invoice resolves to this exact PO row: both entry points reuse one allocation.
    return 'IC-' + hashlib.sha256((po + '\n' + po_detail).encode()).hexdigest()[:24]


def packing_change(count, pack_qty, stock_uom, ordered_qty, retained_qty, reason, expected_modified):
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
        raise ValueError('Explain the packing change (maximum 1000 characters).')
    remaining = float(ordered_qty) - float(retained_qty)
    if remaining <= 0:
        raise ValueError('No order quantity remains for additional or replacement containers.')
    plan = preparation(count, pack_qty, stock_uom, remaining)
    payload = dict(**plan, reason=reason.strip(), expected_modified=str(expected_modified))
    return plan, hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def capture(action, qty, lot, expiry, received_on, today, expiry_required=False, notes=''):
    if action not in ('verify', 'hold'):
        raise ValueError('Choose Check arrival or Put on hold.')
    for value, label, limit in ((lot, 'Supplier lot', 140), (notes, 'Inspection notes', 2000)):
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError(f'{label} must be text within {limit} characters.')
    out = dict(supplier_lot=lot.strip(), notes=notes.strip(), expiry_date=None, received_on=None,
               received_qty=None, status='On hold' if action == 'hold' else 'Arrival checked')
    for key, value in (('expiry_date', expiry), ('received_on', received_on)):
        if value:
            try:
                out[key] = datetime.date.fromisoformat(value).isoformat()
            except (TypeError, ValueError):
                raise ValueError(f'Enter a valid {key.replace("_", " ")}.')
    if out['received_on'] and out['received_on'] > today:
        raise ValueError('The physical arrival date cannot be in the future.')
    if qty not in (None, ''):
        out['received_qty'] = positive(qty, 'Physical quantity')
    if action == 'hold':
        if not out['notes']:
            raise ValueError('Enter the hold reason.')
    else:
        if not out['supplier_lot'] or not out['received_on'] or out['received_qty'] is None:
            raise ValueError('Capture supplier lot, physical quantity and actual arrival date before checking arrival.')
        if expiry_required and not out['expiry_date']:
            raise ValueError('This item requires an expiry date.')
        if out['expiry_date'] and out['expiry_date'] < today:
            raise ValueError('This container is expired. Put it on hold.')
    return out
