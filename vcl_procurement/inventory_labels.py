"""Exact physical label sizes shared by browser responses and native print formats."""
from html import escape
from vcl_procurement.print_brand import brand_icon, label_brand_css


def e(value):
    return escape(str(value if value is not None else ''), quote=True)


def n(value):
    return f'{float(value):,.3f}'.rstrip('0').rstrip('.') if value else '________'


def label_text(value, limit):
    text = str(value or '')
    return e(text if len(text) <= limit else text[:limit-9]+'... scan')


def container_labels_html(rows, format='label'):
    if format not in ('label', 'a4'):
        raise ValueError('Choose 100 x 30 mm labels or A4.')
    if not rows:
        raise ValueError('Prepare container identities before printing.')
    cards = []
    for row in rows:
        cards.append(f'''<section class="ic-label">
        <div class="brand">{brand_icon()}VIMIT CONVERTERS LIMITED</div>
        <h1>{e(row['name'])}</h1><h2 style="font-size:{10 if len(str(row['item_name'])) <= 23 else 9 if len(str(row['item_name'])) <= 30 else 6}pt">{label_text(row['item_name'], 68)}</h2>
        <div class="supplier">Supplier: {label_text(row.get('supplier_name') or row.get('supplier') or '____________________', 36)}</div>
        <div class="pack">NOMINAL PACK: {n(row['nominal_qty'])} {e(row['stock_uom'])}</div>
        <div class="lot" style="font-size:{5.5 if row.get('expiry_date') else 6.7}pt">Lot: {label_text(row.get('supplier_lot') or '____________________', 18 if row.get('expiry_date') else 32)}{' · Exp: '+e(row['expiry_date']) if row.get('expiry_date') else ''}</div>
        <img alt="Open this container in Compass" src="{e(row['qr_uri'])}">
        </section>''')
    if format == 'label':
        pages = ''.join('<article class="ic-sheet">'+card+'</article>' for card in cards)
    else:
        pages = ''.join('<article class="ic-sheet">'+''.join(cards[i:i+18])+'</article>'
                        for i in range(0, len(cards), 18))
    w, h = (100, 30) if format == 'label' else (210, 297)
    return f'''<!doctype html><html><head><meta charset="utf-8"><title>Container labels</title><style>
    @page{{size:{w}mm {h}mm;margin:0}}*{{box-sizing:border-box}}html,body{{margin:0;padding:0;color:#000;font-family:Arial,sans-serif}}
    .print-format{{page-size:Custom;page-width:{w}mm;page-height:{h}mm;width:{w}mm!important;max-width:none!important;padding:0!important;margin:0!important}}
    .ic-sheet{{width:{w}mm;height:{h}mm;page-break-after:always;{'padding:5mm;font-size:0' if format == 'a4' else ''}}}
    .ic-sheet:last-child{{page-break-after:auto}}
    .ic-label{{width:100mm;height:30mm;position:relative;display:inline-block;vertical-align:top;overflow:hidden;line-height:1.1}}
    .ic-label .brand{{position:absolute;left:17.5mm;top:27mm;height:2mm;display:flex;align-items:center;gap:1mm;font-size:4.3pt;font-weight:700;letter-spacing:.2pt}}
    .ic-label h1{{position:absolute;left:17.5mm;top:1mm;font-size:15pt;font-weight:900;letter-spacing:-.3pt;line-height:1.05;margin:0;white-space:nowrap}}
    .ic-label h2{{position:absolute;left:17.5mm;top:8mm;width:54mm;font-weight:800;line-height:1.05;margin:0;height:4.5mm;overflow-wrap:anywhere;overflow:hidden}}
    .ic-label .pack{{position:absolute;left:17.5mm;top:13mm;width:54mm;border-top:.2mm solid black;padding-top:1mm;font-size:9pt;font-weight:800;line-height:1.1;white-space:nowrap}}
    .ic-label .supplier{{position:absolute;left:17.5mm;top:19mm;width:54mm;font-size:6.7pt;white-space:nowrap;overflow:hidden}}
    .ic-label .lot{{position:absolute;left:17.5mm;top:23mm;width:54mm;white-space:nowrap;overflow:hidden}}
    .ic-label img{{position:absolute;left:75mm;top:7mm;width:22mm;height:22mm}}
    {'.ic-label{border:.15mm solid #aaa}' if format == 'a4' else ''}
    .tools{{padding:12px;background:#f3f3f3;font:14px Arial}}.tools button{{padding:8px 14px;margin:0 8px}}
    @media print{{.tools{{display:none}}}}
    {label_brand_css()}.ic-label .brand .vcl-print-mark{{margin-right:0;width:2mm;height:2mm}}</style></head><body><div class="tools">Print Actual size / 100%, headers and footers off. Keep the original supplier safety label.<button onclick="window.print()">Print</button></div>{pages}</body></html>'''


def reel_labels100_html(data):
    # Compatibility entry point for older native formats; the standard is 100 x 70.
    from vcl_procurement.reel_identity_print import labels_html, native_fragment
    return native_fragment(labels_html(data))
