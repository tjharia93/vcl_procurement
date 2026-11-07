"""Price-free native ERPNext print layouts; no writes or Frappe dependencies."""
from html import escape
import re


def e(value):
    return escape(str(value if value is not None else ''), quote=True)


def number(value):
    return f'{float(value):,.3f}'.rstrip('0').rstrip('.') if value else ''


def styles(landscape=False):
    orientation = 'landscape' if landscape else 'portrait'
    width, height = (297, 210) if landscape else (210, 297)
    return f'''<style>
    @page{{size:A4 {orientation};margin:0}}
    .print-format{{page-size:A4;orientation:{orientation};margin-top:0;margin-bottom:0;margin-left:0;margin-right:0;padding:0!important;width:{width}mm!important;max-width:none!important;font-family:Arial,sans-serif!important;color:#172d36}}
    .reel-sheet,.reel-sheet *{{box-sizing:border-box}}
    .reel-sheet{{width:{width}mm;height:{height}mm;padding:5mm;position:relative;page-break-after:always;page-break-inside:avoid;background:white}}
    .reel-sheet:last-child{{page-break-after:auto}}
    .reel-sheet table{{border-collapse:collapse;table-layout:fixed;margin:0}}
    .reel-sheet h1,.reel-sheet h2,.reel-sheet p{{margin:0;padding:0}}
    .reel-sheet .brand{{font-size:7pt;font-weight:bold}}
    .reel-sheet .state{{font-size:5.5pt;margin:.6mm 0;white-space:nowrap}}
    .reel-sheet h1{{font-size:15pt;line-height:1.2;padding:1.2mm 1.5mm;margin:.8mm 0 1mm;background:#172d36!important;color:white!important;white-space:nowrap;-webkit-print-color-adjust:exact}}
    .reel-sheet h2{{font-size:6.5pt;line-height:1.2;max-height:6mm;overflow:hidden;font-weight:bold;margin-bottom:.5mm}}
    .reel-pair{{height:95.666mm;margin-bottom:0;page-break-inside:avoid}}
    .reel-pair:last-child{{margin-bottom:0}}
    .reel-card{{display:inline-block;vertical-align:top;width:100mm;height:95.666mm;border:0;border-top:.2mm solid #172d36;border-left:.2mm solid #172d36;padding:3mm;position:relative}}
    .reel-card+.reel-card{{margin-left:0;border-right:.2mm solid #172d36}}
    .reel-pair:last-child .reel-card{{border-bottom:.2mm solid #172d36}}
    .reel-sheet .main{{height:23mm;position:relative;padding-right:25mm;font-size:6pt;line-height:1.15}}
    .reel-sheet .main img{{position:absolute;right:0;top:0;width:23mm;height:23mm}}
    .reel-sheet .main strong{{font-size:15pt;display:block;margin:.4mm 0}}
    .reel-sheet .supplier{{font-size:7pt;font-weight:bold;margin-bottom:.8mm}}
    .reel-sheet .ref{{font-size:6.5pt;line-height:1.15;margin-top:.8mm}}
    .reel-sheet .ref span{{display:inline-block;width:25mm;color:#52636b}}
    .reel-sheet .received{{font-size:6.5pt;margin-top:2mm}}
    .reel-sheet .receive-box{{display:inline-block;vertical-align:top;width:45.7mm;border:.2mm solid #80949c;padding:1mm 1.5mm}}
    .reel-sheet .receive-box+.receive-box{{margin-left:2mm}}
    .reel-sheet .receive-box span{{display:block;font-size:6.5pt;font-weight:bold}}
    .reel-sheet .receive-value{{height:5mm;font-size:9pt;padding-top:.5mm}}
    .reel-sheet .card-foot{{position:absolute;left:3mm;right:3mm;bottom:1.8mm;font-size:5pt}}
    .reel-sheet .movement-info{{font-size:6pt;line-height:1.2}}
    .reel-sheet .started{{font-size:6.5pt;margin:1mm 0}}
    .reel-sheet .movement{{width:100%}}
    .reel-sheet .movement th,.reel-sheet .movement td{{border:.2mm solid #80949c;padding:.6mm!important;font-size:6.5pt;text-align:left;line-height:1.15}}
    .reel-sheet .movement th{{height:6.5mm;background:#eef3f4!important}}
    .reel-sheet .movement td{{height:7.5mm}}
    .reel-sheet .material-spec{{font-size:15pt;font-weight:bold;line-height:1.2;margin:1mm 0 2mm}}
    </style>'''


def labels_html(data):
    reels = data['reels']
    if not reels:
        raise ValueError('Prepare permanent reel tags before printing.')
    if data.get('docstatus') == 2:
        raise ValueError('Cancelled receipts cannot print active reel tags.')
    pages = []
    for start in range(0, len(reels), 3):
        pairs = []
        for r in reels[start:start + 3]:
            qty = number(r.get('original_qty')) or '________'
            def heading(title, short=False):
                display_id = re.sub(r'^VCL-RL-(\d{4})-(\d+)$', r'VCL/\1/\2', r['batch_id'])
                material = f"RW {e(r.get('width_mm'))} mm · {e(r.get('gsm'))} GSM"
                item = '' if short else f"<h2>{e(r.get('item_name') or r.get('item_code'))}</h2>"
                return f'<div class="brand">{title}</div><div class="state">VIMIT CONVERTERS LTD</div><h1>{e(display_id)}</h1><div class="material-spec">{material}</div>{item}'
            def ref(label, value):
                return f'<div class="ref"><span>{label}</span><b>{e(value or "____________________")}</b></div>'
            sources = ref('Supplier invoice', r.get('supplier_invoice'))
            if r.get('order_type') == 'Import':
                sources += ref('BL Number', r.get('bl_number')) + ref(e(r.get('container_label') or 'Container Number'), r.get('container'))
            else:
                sources += ref('PO Number', r.get('purchase_order'))
            identity = heading('PERMANENT REEL IDENTITY') + f'''<div class="main"><img alt="Open this reel record" src="{e(r.get('qr_uri'))}">
                <div class="supplier">{e(r.get('supplier'))}</div>{e(r.get('weight_basis'))}<strong>{qty} kg</strong>{e(r.get('verification'))}
                {ref('Supplier reel no.', r.get('supplier_reel_no'))}</div>{sources}
                <div class="received"><div class="receive-box"><span>Received on (date)</span><div class="receive-value">{e(r.get('received_on'))}</div></div><div class="receive-box"><span>Received by (name / initials)</span><div class="receive-value"></div></div></div>
                <div class="card-foot">Scan to record arrival. Stock posting is separate.</div>'''
            movement = heading('REEL MOVEMENT LOG', True) + f'''<div class="movement-info">Original quantity: {qty} kg</div>
                <div class="started">Log sheet no. ______ &nbsp; Started on ______________</div><table class="movement">
                <colgroup><col style="width:16%"><col style="width:36%"><col style="width:16%"><col style="width:16%"><col style="width:16%"></colgroup>
                <thead><tr><th>Date</th><th>Job / destination</th><th>Out<br>kg</th><th>Return<br>kg</th><th>Initials</th></tr></thead><tbody>
                {''.join('<tr><td></td><td></td><td></td><td></td><td></td></tr>' for _ in range(6))}</tbody></table>
                <div class="card-foot">Record kg. Enter movements in ERPNext. Retain completed sheets.</div>'''
            pairs.append(f'<div class="reel-pair"><section class="reel-card">{identity}</section><section class="reel-card">{movement}</section></div>')
        pages.append('<article class="reel-sheet">' + ''.join(pairs) + '</article>')
    return styles() + ''.join(pages)


def receiving_list_html(data):
    if not data['reels']:
        raise ValueError('Prepare permanent reel tags before printing.')
    groups = {}
    for reel in data['reels']:
        groups.setdefault((reel.get('item_code'), reel.get('width_mm'), reel.get('gsm')), []).append(reel)
    pages = []
    for group in groups.values():
        group.sort(key=lambda r: r['name'])
        for start in range(0, len(group), 16):
            pages.append((group, start, group[start:start + 16]))
    html = [styles(True), '''<style>
    .reel-sheet{padding:9mm 10mm}.reel-sheet header{height:32mm;padding-right:34mm;position:relative}
    .reel-sheet header h1{background:none!important;color:#172d36!important;font-size:16pt;padding:0;margin:0 0 1mm}
    .reel-sheet header h2{font-size:10pt;height:auto;margin-bottom:1mm}
    .reel-sheet header img{position:absolute;right:0;top:0;width:28mm;height:28mm}
    .reel-sheet .facts{font-size:8pt;line-height:1.5}.reel-sheet .group{border-top:.3mm solid #172d36;font-size:9pt;padding:2mm 0 1mm}
    .reel-sheet .arrival{font-size:8pt;margin-bottom:2mm}.reel-sheet .register{width:277mm}
    .reel-sheet .register th,.reel-sheet .register td{border:.2mm solid #80949c;padding:1mm!important;line-height:1.2;text-align:left;font-size:8pt;vertical-align:middle}
    .reel-sheet .register th{font-size:7pt;height:9mm;background:#eef3f4!important}.reel-sheet .register td{height:7.7mm}
    .reel-sheet .register .id{font-size:7.5pt;font-weight:bold;white-space:nowrap}.reel-sheet .register .number{text-align:right}
    .reel-sheet .row-note{font-size:7pt;max-height:5.5mm;overflow:hidden;word-wrap:break-word}
    .reel-sheet footer{position:absolute;left:10mm;right:10mm;bottom:5mm;font-size:7pt}
    </style>''']
    for page, (group, start, rows) in enumerate(pages, 1):
        first = group[0]
        item = next((i for i in data['items'] if i['item_code'] == first.get('item_code')), {})
        qr = data.get('delivery_qr_uri') or ''
        qr_html = f'<img alt="Open delivery checklist" src="{e(qr)}">' if qr.startswith('data:image/svg+xml;base64,') else ''
        html.append(f'''<article class="reel-sheet"><header><h1>FULL REEL RECEIVING LIST</h1><h2>VIMIT CONVERTERS LTD · {e(data.get('shipment') or data['invoice'])} · {len(data['reels'])} reels</h2>
            <div class="facts"><b>Supplier:</b> {e(data['supplier'])} &nbsp; <b>Invoice:</b> {e(data['supplier_invoice'])} &nbsp; <b>PO:</b> {e(data['po'])}<br>
            <b>BL:</b> {e(data.get('bl_number'))} &nbsp; <b>Shipment containers:</b> {e(' / '.join(c['number'] for c in data['containers']))}</div>{qr_html}</header>
            <div class="group"><b>{e(first.get('width_mm'))} mm · {e(first.get('gsm'))} GSM · {e(item.get('item_name') or first.get('item_code'))}</b><br>
            {len(group)} prepared reels in this size · Rows {start + 1}–{start + len(rows)} · Invoice quantity: {e(item.get('qty'))} {e(item.get('uom'))}</div>
            <div class="arrival">Actual VCL arrival date: __________________ &nbsp; Warehouse: ______________________________ &nbsp; Receiving team: ______________________________</div>
            <table class="register"><colgroup>{''.join(f'<col style="width:{w}mm">' for w in [8,43,38,24,24,24,82,34])}</colgroup>
            <thead><tr><th>No.</th><th>Permanent VCL reel ID</th><th>Supplier reel number</th><th>Supplier label<br>NET kg</th><th>Supplier label<br>GROSS kg</th><th>Measured<br>GROSS kg</th><th>Condition / discrepancies / notes</th><th>Received date<br>/ initials</th></tr></thead><tbody>''')
        for i, r in enumerate(rows, start + 1):
            html.append(f'''<tr><td>{i}</td><td class="id">{e(r['name'])}</td><td>{e(r.get('supplier_reel_no'))}</td>
                <td class="number">{number(r.get('declared_net_kg'))}</td><td class="number">{number(r.get('declared_gross_kg'))}</td><td class="number">{number(r.get('measured_gross_kg'))}</td>
                <td><div class="row-note">{'<b>ON HOLD · </b>' if r.get('status') == 'On hold' else ''}{e(r.get('notes'))}</div></td><td>{e(r.get('received_on'))}</td></tr>''')
        html.append(f'''</tbody></table><footer>Capture physical supplier labels; unknown values stay blank. Physical receiving worksheet; stock posting is separate.
            <b style="float:right">Page {page} / {len(pages)}</b></footer></article>''')
    return ''.join(html)
