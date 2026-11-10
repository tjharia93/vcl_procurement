"""Approved 100 x 30 mm bundle design, with a 15 mm rightward text offset."""
from html import escape
import re
from vcl_procurement.print_brand import brand_icon, label_brand_css


def number(value):
    return f'{float(value):g}'


def material_name(row):
    """Remove only a matching specification suffix already printed separately."""
    material = str(row['material']).strip()
    def numeric(value):
        return re.escape(number(value)) + (r'(?:\.0+)?' if float(value).is_integer() else '')
    suffix = (r'\s*[-–·,]?\s*' + numeric(row['width_mm']) + r'\s*[x×]\s*' +
        numeric(row['length_mm']) + r'\s*mm\s*' + numeric(row['gsm']) +
        r'\s*gsm\s*' + re.escape(str(row['colour']).strip()) + r'\s*$')
    compact = re.sub(suffix, '', material, flags=re.IGNORECASE).strip()
    if compact and compact != material:
        compact = re.sub(r'\s+REAMS$', '', compact, flags=re.IGNORECASE).strip()
        return compact or material
    return material


def thermal_labels_html(rows):
    e = lambda value: escape(str(value if value is not None else ''), quote=True)
    cards = []
    for row in rows:
        title = material_name(row).upper() + (' · '+str(row['colour']).upper() if row.get('colour') else '')
        # Long saved descriptions wrap inside their own area, never across the QR.
        size = 11.5 if len(title) <= 23 else 9 if len(title) <= 30 else 7.5 if len(title) <= 40 else 5.5
        cards.append(f'''<article class="bundle-sheet"><section class="bundle-label">
        <div class="brand">{brand_icon()}VIMIT CONVERTERS LIMITED</div>
        <h2 style="font-size:{size}pt">{e(title)}</h2>
        <div class="spec">{e(number(row['width_mm']))} × {e(number(row['length_mm']))} mm · {e(number(row['gsm']))} GSM</div>
        <div class="contents">{e(number(row['reams_per_bundle']))} REAMS / {e(number(row['total_sheets']))} SHEETS</div>
        <div class="packing">{e(number(row['sheets_per_ream']))} sheets per ream</div>
        <h1>{e(row['name'])}</h1>
        <img class="qr" alt="Open bundle record" src="{e(row['qr_uri'])}">
        </section></article>''')
    return '''<!doctype html><html><head><meta charset="utf-8"><title>Bundle labels</title><style>
    @page{size:100mm 30mm;margin:0}*{box-sizing:border-box}html,body{margin:0;padding:0;font-family:Arial,sans-serif;background:white;color:black}
    .print-format{page-size:Custom;page-width:100mm;page-height:30mm;width:100mm!important;max-width:none!important;padding:0!important;margin:0!important}
    .bundle-sheet{width:100mm;height:30mm;page-break-after:always;page-break-inside:avoid}.bundle-sheet:last-child{page-break-after:auto}
    .bundle-label{position:relative;width:100mm;height:30mm;overflow:hidden}
    .brand{position:absolute;left:17.5mm;top:1.2mm;height:3mm;display:flex;align-items:center;gap:1mm;font-size:5.5pt;font-weight:700;letter-spacing:.2pt}
    .bundle-label h2{position:absolute;left:17.5mm;top:5.5mm;width:54mm;margin:0;font-weight:800;line-height:1.05;overflow-wrap:anywhere}
    .spec{position:absolute;left:17.5mm;top:10.6mm;width:54mm;font-size:9pt;font-weight:700;line-height:1.1;white-space:nowrap}
    .contents{position:absolute;left:17.5mm;top:15.1mm;width:54mm;border-top:.2mm solid #000;padding-top:1.2mm;font-size:10.5pt;font-weight:800;line-height:1.1;white-space:nowrap}
    .packing{position:absolute;left:17.5mm;top:21.4mm;font-size:7pt;line-height:1.1}
    .bundle-label h1{position:absolute;left:17.5mm;top:26mm;margin:0;font-size:7.2pt;font-weight:600;letter-spacing:.15pt;line-height:1.1;white-space:nowrap}
    .qr{position:absolute;left:75mm;top:4mm;width:22mm;height:22mm;object-fit:contain}
    .tools{padding:12px;font:14px Arial}.tools button{padding:8px 16px;margin:0 8px}@media print{.tools{display:none}}
    '''+label_brand_css()+'''.brand .vcl-print-mark{margin-right:0}
    </style></head><body><div class="tools">100 × 30 mm labels. Print Actual size / 100%, headers and footers off.<button onclick="window.print()">Print</button></div>'''+''.join(cards)+'</body></html>'
