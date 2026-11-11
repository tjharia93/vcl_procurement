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
    if not data['reels'] or data.get('docstatus') == 2:
        raise ValueError('Prepare active reel identities before printing.')
    cards = []
    for r in data['reels']:
        heading = f'<div class="brand">{brand_icon()}VIMIT CONVERTERS</div><h1>{e(r["batch_id"])}</h1>'
        identity = f'''<section class="rl-label">{heading}<h2>{e(r.get('item_name') or r.get('item_code'))}</h2>
        <div class="spec">RW {e(r.get('width_mm') or '____')} mm | {e(r.get('gsm') or '____')} GSM</div>
        <div class="quantity"><small>ORIGINAL QUANTITY</small><strong>{n(r.get('original_qty'))} kg</strong><small>{e(r.get('weight_basis'))}</small></div>
        <img alt="Open this reel in Compass" src="{e(r['qr_uri'])}">
        <div class="refs">Supplier reel: {e(r.get('supplier_reel_no') or '________________')}<br>
        Supplier invoice: {e(r.get('supplier_invoice'))}<br>
        {'BL' if r.get('order_type') == 'Import' else 'PO'}: {e(r.get('bl_number') if r.get('order_type') == 'Import' else r.get('purchase_order'))}</div>
        <div class="received"><div>Received on<br>{e(r.get('received_on') or '________________')}</div><div>Received by<br>________________</div></div>
        <footer>Permanent ID. Scan for current status and balance.</footer></section>'''
        movement = f'''<section class="rl-label log">{heading}<h2>REEL MOVEMENT LOG</h2>
        <p>RW {e(r.get('width_mm') or '____')} mm | {e(r.get('gsm') or '____')} GSM</p>
        <table><colgroup><col style="width:16%"><col style="width:36%"><col style="width:16%"><col style="width:16%"><col style="width:16%"></colgroup>
        <tr><th>Date</th><th>Job / destination</th><th>Out kg</th><th>Return kg</th><th>Initials</th></tr>
        {''.join('<tr><td></td><td></td><td></td><td></td><td></td></tr>' for _ in range(6))}</table>
        <footer>Record movements in Compass. Retain completed sheets.</footer></section>'''
        cards.extend((identity, movement))
    return '''<style>@page{size:100mm 100mm;margin:0}
    *{box-sizing:border-box}html,body{margin:0;padding:0;font-family:Arial,sans-serif;color:#000}
    .print-format{page-size:Custom;page-width:100mm;page-height:100mm;width:100mm!important;max-width:none!important;padding:0!important;margin:0!important}
    .rl-label{width:100mm;height:100mm;padding:5mm;position:relative;page-break-after:always;overflow:hidden}
    .rl-label:last-child{page-break-after:auto}.brand{font-size:8pt;font-weight:bold}h1{font-size:17pt;margin:3mm 0;white-space:nowrap}h2{font-size:11pt;height:9mm;line-height:1.1;overflow-wrap:anywhere;margin:2mm 0}
    .spec{font-size:17pt;font-weight:bold;border-top:.25mm solid black;border-bottom:.25mm solid black;padding:3mm 0;white-space:nowrap}
    .quantity{margin-top:4mm;width:53mm}.quantity small{display:block;font-size:7pt}.quantity strong{display:block;font-size:23pt;margin:1mm 0}
    .rl-label img{position:absolute;right:5mm;top:48mm;width:28mm;height:28mm}.refs{margin-top:3mm;font-size:7pt;line-height:1.5;width:55mm;overflow-wrap:anywhere}
    .received{position:absolute;left:5mm;right:5mm;bottom:7mm;display:flex;gap:4mm;font-size:7pt}.received div{width:50%;line-height:1.8}
    footer{position:absolute;left:5mm;bottom:3mm;font-size:5.5pt}.log h2{height:auto;margin:3mm 0}.log p{font-size:9pt}.log table{width:100%;border-collapse:collapse;table-layout:fixed}.log th,.log td{border:.2mm solid #333;font-size:6pt;padding:.8mm;text-align:left;height:8mm}.log th{height:7mm}
    ''' + label_brand_css() + '</style>' + ''.join(cards)
