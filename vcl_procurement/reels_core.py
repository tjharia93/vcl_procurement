"""Pure quantity rules for physical reels; quantities are always in stock UOM."""
import math

WEIGHT_BASES = ('Supplier-declared weight', 'Measured net weight')


def number(value, label, *, positive=False):
    try:
        value = float(value or 0)
    except (ValueError, TypeError):
        raise ValueError(f'{label} must be a number.')
    if not math.isfinite(value) or value < 0 or (positive and value <= 0):
        raise ValueError(f'{label} must be a finite {"positive" if positive else "non-negative"} number.')
    return value


def receipt_conversion(uom, stock_uom, conversion_factor=1):
    """Allow fixed mass conversions; never invent a kg-per-reel conversion."""
    if stock_uom != 'Kg':
        raise ValueError('This receiving flow currently supports paper reels stocked in Kg.')
    if uom == 'Kg':
        expected = 1
    elif uom in ('Tonne', 'Tonnes', 'Metric Ton'):
        expected = 1000
    else:
        raise ValueError(f'Reel receiving does not support {uom} to {stock_uom}.')
    if abs(number(conversion_factor, 'Conversion factor', positive=True) - expected) > 1e-9:
        raise ValueError(f'{uom} to {stock_uom} must use conversion factor {expected}.')
    return expected


def clean_reel(raw):
    gross = number(raw.get('gross_weight'), 'Gross weight')
    core = number(raw.get('core_weight'), 'Core weight')
    declared = number(raw.get('declared_weight'), 'Declared weight')
    net = number(raw.get('net_weight'), 'Net weight')
    basis = raw.get('weight_basis') or 'Measured net weight'
    if basis not in WEIGHT_BASES:
        raise ValueError('Choose a valid weight basis.')
    if gross and core >= gross:
        raise ValueError('Core weight must be less than gross weight.')
    if basis == 'Supplier-declared weight':
        quantity = number(declared, 'Supplier-declared weight', positive=True)
        # Do not manufacture a measured net quantity from a packing-list gross weight.
        net = 0
    else:
        quantity = number(net or (gross-core), 'Net weight', positive=True)
        if gross and abs(quantity - (gross-core)) > .005:
            raise ValueError('Net weight must equal gross weight minus core weight.')
        net = quantity
    inspection = raw.get('inspection_status') or 'Pending inspection'
    if inspection not in ('Accepted', 'Pending inspection', 'Quality hold'):
        raise ValueError('Choose a valid inspection status.')
    return dict(supplier_reel_no=str(raw.get('supplier_reel_no') or '').strip(),
        gross_weight=gross, core_weight=core, net_weight=net, declared_weight=declared,
        stock_qty=quantity, weight_basis=basis,
        verification_status='Pending verification' if basis == 'Supplier-declared weight' else 'Verified',
        meters=number(raw.get('meters'), 'Metres'),
        width_mm=number(raw.get('width_mm'), 'Width'), gsm=number(raw.get('gsm'), 'GSM'),
        diameter_mm=number(raw.get('diameter_mm'), 'Diameter'), inspection_status=inspection)
