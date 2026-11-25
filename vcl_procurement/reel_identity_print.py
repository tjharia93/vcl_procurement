"""Standard identity-only 100 x 70 mm reel labels, independent of stock UOM.

Every print path reads authorized source records and reuses their permanent IDs.
No stock mutation or ID allocation belongs here.

Privacy (Tanuj, 25/11/2026): the label goes out with the reel, so it carries no supplier,
PO, BL or container, and its QR holds only the reel ID (no web address). The bottom strip
shows the mill's own reel number (already on the reel's mill tag, used to match the label to
the reel at offloading) and the received date, or a blank line to write it in.
"""
from html import escape
import base64
import io
import re
import math
from datetime import datetime

BRIGHTNESS = re.compile(
    r'\b(?:(\d+(?:\.\d+)?)\s*%\s*brightness\b|brightness\s*[:=-]?\s*(\d+(?:\.\d+)?)\s*%)',
    re.I,
)


def e(value):
    return escape(str(value or ''), quote=True)


def n(value):
    return f'{float(value):g}'


def purchase_reference(row):
    """Print only an explicitly supplied purchase-note line for existing stock.

    Inspection notes never become label text. This is purchase provenance,
    not the physical registration/arrival date and not an accounting voucher.
    """
    if not row.get('existing_stock'):
        return ''
    for line in str(row.get('notes') or '').splitlines():
        match=re.fullmatch(r'Bought on (\d{2}/\d{2}/\d{4}) - ([^\r\n]{1,100})',line.strip(),re.I)
        if match:
            try:
                datetime.strptime(match[1],'%d/%m/%Y')
            except ValueError:
                continue
            return match[1]+' - '+match[2].strip()
    return ''


def material_name(row):
    text = str(row.get('item_name') or row.get('item_code') or '')
    text = re.split(r'\s*-\s*Reel(?:\s|-)', text, maxsplit=1, flags=re.I)[0]
    if ' - ' in text and text.split(' - ',1)[0].strip() in str(row.get('item_code') or ''):
        text = text.split(' - ', 1)[1]
    text = re.sub(r'\s*-\s*\d+(?:\.\d+)?\s*mm\s*$', '', text, flags=re.I)
    if row.get('gsm'):
        text = re.sub(r'\b\d+(?:\.\d+)?\s*gsm\b', '', text, flags=re.I)
    text = BRIGHTNESS.sub('', text)
    text = re.sub(r'\(\s*Yellow\s+Back\s*\)', '', text, flags=re.I)
    # Removing specifications must not leave empty dash-separated title parts.
    return ' - '.join(' '.join(part.split()) for part in re.split(r'\s+-', text) if part.strip())


def brightness_text(row):
    """Only print an explicitly stated percentage; item-code suffixes are ambiguous."""
    match = BRIGHTNESS.search(str(row.get('item_name') or ''))
    return f'{n(match[1] or match[2])}% BRIGHTNESS' if match else ''


def registered_net_weight(row):
    """Print a known measured registration weight, never an arbitrary stock quantity."""
    if not row.get('existing_stock') or not row.get('weight_known'):
        return ''
    try:
        weight = float(row.get('net_kg'))
    except (TypeError, ValueError):
        return ''
    return f'{n(weight)} kg' if math.isfinite(weight) and weight > 0 else ''


def _positive(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    return number if math.isfinite(number) and number > 0 else 0


def quantity_box(row):
    """Caption and text for the quantity box (Tanuj, 18/11/2026).

    The quantity is printed only when that reel's own quantity is on record: a measured
    registration weight, or the kg / metres known in advance for that reel. Otherwise the box
    is left blank for receiving to write in by hand. A stock or order quantity is never used.
    """
    registered = registered_net_weight(row)
    if registered:
        return 'NET WEIGHT AT REGISTRATION', registered
    if row.get('qty_unit') == 'm':
        length = _positive(row.get('known_m'))
        return ('LENGTH', f'{n(length)} m') if length else ('LENGTH (m)', '')
    weight = _positive(row.get('known_kg'))
    return ('WEIGHT', f'{n(weight)} kg') if weight else ('WEIGHT (kg)', '')


def id_qr(value):
    """QR holding only the ID text, so a phone that scans it shows the ID and nothing else."""
    from pyqrcode import create
    stream = io.BytesIO()
    create(str(value), error='M').svg(stream, scale=4, quiet_zone=4)
    return 'data:image/svg+xml;base64,' + base64.b64encode(stream.getvalue()).decode()


def label_date(value):
    try:
        return datetime.strptime(str(value or '')[:10], '%Y-%m-%d').strftime('%d/%m/%Y')
    except ValueError:
        return ''


def bottom_refs(row):
    """Mill reel number, then the received date (blank for writing in) or, for existing stock, the purchase date."""
    refs = [('MILL REEL NO.', str(row['supplier_reel_no']))] if row.get('supplier_reel_no') else []
    bought = purchase_reference(row).split(' - ', 1)[0]
    if bought:
        refs.append(('BOUGHT ON', bought))
    else:
        refs.append(('RECEIVED', label_date(row.get('received_on'))))
    return refs


def labels_html(data, format='label'):
    if format not in ('label', 'a4'):
        raise ValueError('Choose 100 x 70 mm label stock or A4.')
    if not data.get('reels'):
        raise ValueError('Prepare reel identities before printing labels.')
    if data.get('docstatus') == 2:
        raise ValueError('Cancelled sources cannot print active reel labels.')
    identities = [row.get('batch_id') for row in data['reels']]
    if any(not value for value in identities) or len(set(identities)) != len(identities):
        raise ValueError('Select distinct registered reel identities before printing.')
    cards = []
    for r in data['reels']:
        brightness = brightness_text(r)
        brightness_html = f'<div class="brightness">{e(brightness)}</div>' if brightness else ''
        brightness_attr = ' data-brightness="true"' if brightness else ''
        caption, quantity = quantity_box(r)
        weight_html = f'<div class="qty-box"><span>{e(caption)}</span> <b>{e(quantity)}</b></div>'
        gsm = f"<span class='gsm'><i>|</i>{n(r['gsm'])} GSM</span>" if r.get('gsm') else ''
        backing = ' | Yellow back' if 'yellow back' in (r.get('item_name') or '').lower() else ''
        refs = bottom_refs(r)
        reference_html = ''.join(f'<div><span>{key}</span><b>{e(value)}</b></div>' if value
            else f'<div><span>{key}</span><b class="write-in"></b></div>' for key,value in refs)
        title_size = 13 if len(material_name(r)) <= 18 else 10 if len(material_name(r)) <= 30 else 8
        cards.append(f'''<section class="po-reel-label"{brightness_attr}>
        <div class="brand">REEL</div>
        <h1>{e(r['batch_id'])}</h1><h2 style="font-size:{title_size}pt">{e(material_name(r))}</h2>
        <div class="material-code">{e(r.get('item_code'))}{backing}</div>
        {brightness_html}
        <div class="width-caption">REEL WIDTH</div>
        <div class="spec"><strong>{n(r['width_mm']) if r.get('width_mm') else '—'} <small>mm</small></strong>{gsm}</div>
        {weight_html}
        <img class="qr" alt="Reel ID" src="{e(id_qr(r['batch_id']))}">
        <div class="scan-caption">SCAN IN COMPASS</div>
        <div class="refs{' compact' if len(refs)>2 else ''}">{reference_html}</div></section>''')
    if format == 'label':
        pages = ''.join('<article class="label-sheet">'+card+'</article>' for card in cards)
    else:
        pages = ''.join('<article class="label-sheet">'+''.join(cards[i:i+8])+'</article>'
            for i in range(0, len(cards), 8))
    w,h = (100,70) if format == 'label' else (210,297)
    return f'''<!doctype html><html><head><meta charset="utf-8"><title>Reel labels - {e(data.get('receipt'))}</title><style>
    @page{{size:{w}mm {h}mm;margin:0}}*{{box-sizing:border-box}}html,body{{margin:0;padding:0;font-family:Arial,sans-serif;color:#000}}
    .print-format{{page-size:Custom;page-width:{w}mm;page-height:{h}mm;width:{w}mm!important;max-width:none!important;padding:0!important;margin:0!important}}
    .label-sheet{{width:{w}mm;height:{h}mm;page-break-after:always;page-break-inside:avoid;{'padding:5mm;font-size:0' if format=='a4' else ''}}}
    .label-sheet:last-child{{page-break-after:auto}}
    .po-reel-label{{width:100mm;height:70mm;position:relative;display:inline-block;vertical-align:top;overflow:hidden;background:#fff}}
    .po-reel-label,.po-reel-label *{{font-family:Arial,sans-serif!important}}
    .po-reel-label .brand{{position:absolute;left:4mm;right:4mm;top:3.5mm;height:4mm;display:flex;align-items:center;gap:1.4mm;font-size:6.5pt;font-weight:bold;line-height:1}}
    .po-reel-label h1{{position:absolute;left:4mm;right:4mm;top:10mm;margin:0;padding:0 0 2mm;border-bottom:.25mm solid #000;font-size:16pt;font-weight:800;line-height:1.05;white-space:nowrap}}
    .po-reel-label h2{{position:absolute;left:4mm;top:22mm;width:54mm;max-height:6.3mm;overflow:hidden;margin:0;font-weight:800;line-height:1.1}}
    .po-reel-label .material-code{{position:absolute;left:4mm;top:29mm;width:54mm;max-height:6mm;overflow:hidden;overflow-wrap:anywhere;font-size:7pt;line-height:1.2}}
    .po-reel-label .brightness{{position:absolute;left:4mm;top:35mm;width:54mm;height:4mm;font-size:8.5pt;font-weight:bold;line-height:1.1;white-space:nowrap}}
    .po-reel-label[data-brightness] .width-caption{{top:40mm}}
    .po-reel-label[data-brightness] .spec{{top:44mm}}
    .po-reel-label[data-brightness] .spec strong{{font-size:17pt}}
    .po-reel-label[data-brightness] .spec small{{font-size:9pt}}
    .po-reel-label .qty-box{{position:absolute;left:4mm;top:50.2mm;width:54mm;height:7mm;border:.3mm solid #000;padding:0 1.5mm;display:flex;align-items:center;justify-content:space-between;gap:2mm;line-height:1;white-space:nowrap}}
    .po-reel-label .qty-box span{{font-size:5.5pt;font-weight:bold}}
    .po-reel-label .qty-box b{{font-size:11pt}}
    .po-reel-label .width-caption{{position:absolute;left:4mm;top:37mm;font-size:6pt;font-weight:bold;line-height:1}}
    .po-reel-label .spec{{position:absolute;left:4mm;top:41mm;width:54mm;display:flex;align-items:baseline;gap:4mm;line-height:1}}
    .po-reel-label .spec strong{{font-size:22pt;font-weight:900;white-space:nowrap}}
    .po-reel-label .spec small{{font-size:11pt;font-weight:bold}}
    .po-reel-label .gsm{{font-size:10pt;font-weight:bold;white-space:nowrap}}
    .po-reel-label .gsm i{{font-style:normal;font-weight:normal;margin-right:2mm}}
    .po-reel-label .qr{{position:absolute;right:4mm;top:23mm;width:32mm;height:32mm}}
    .po-reel-label .scan-caption{{position:absolute;right:4mm;top:55.3mm;width:32mm;text-align:center;font-size:5pt;font-weight:bold;line-height:1}}
    .po-reel-label .refs{{position:absolute;left:4mm;right:4mm;top:58mm;border-top:.15mm solid #000;padding-top:1.8mm;font-size:7pt;line-height:1.15}}
    .po-reel-label .refs div{{height:4.2mm;display:flex;align-items:baseline;gap:2mm;white-space:nowrap}}
    .po-reel-label .refs span{{width:17mm;flex:none;font-size:5.5pt;font-weight:bold}}
    .po-reel-label .refs b.write-in{{width:32mm;height:3.4mm;border-bottom:.15mm solid #000}}
    .po-reel-label .refs b{{min-width:0;overflow:hidden;text-overflow:ellipsis;font-size:7pt;font-weight:normal}}
    .po-reel-label .refs.compact{{padding-top:1.2mm}}
    .po-reel-label .refs.compact div{{height:3.1mm}}
    .po-reel-label .refs.compact b{{font-size:6.5pt}}
    .tools{{padding:14px;background:#eef2f5;font:14px Arial;text-align:center}}
    .tools button{{padding:8px 18px;margin-left:12px;cursor:pointer}}
    @media screen{{body{{background:#e5e7eb}}.label-sheet{{margin:16px auto;background:#fff;box-shadow:0 1px 6px #aab}}}}
    @media print{{.tools{{display:none}}.label-sheet{{margin:0;box-shadow:none}}}}
    </style></head><body><div class="tools"><b>{len(cards)} reel identity labels - 100 × 70 mm</b> | {e(data.get('receipt'))}<br>
    Print at Actual size / 100%. Headers and footers off.{' A4: eight labels per sheet.' if format=='a4' else ''}<button onclick="window.print()">Print</button></div>{pages}</body></html>'''


def native_fragment(html):
    return html[html.index('<style>'):html.index('</style>')+8]+html[html.index('<article'):html.index('</body>')]


def add_known_quantities(data):
    """Attach each reel's own known quantity from its Reel Intake record, for the quantity box.

    Kg comes from the supplier net kg entered for that reel. Material stocked in Square Meter
    uses the known length in metres (a site custom field, so it is read only when it exists).
    """
    import frappe
    rows = [r for r in (data or {}).get('reels') or [] if r.get('batch_id')]
    if not rows or not frappe.db.exists('DocType', 'Reel Intake'):
        return data
    fields = ['name', 'item_code', 'declared_net_kg']
    has_length = bool(frappe.get_meta('Reel Intake').has_field('custom_length_m'))
    if has_length:
        fields.append('custom_length_m')
    known = {r.name: r for r in frappe.get_all('Reel Intake', filters={'name': ['in', [r['batch_id'] for r in rows]]},
        fields=fields, limit_page_length=0)}
    units = {}
    for row in rows:
        reel = known.get(row['batch_id'])
        if not reel:
            continue
        code = reel.get('item_code') or row.get('item_code')
        if code and code not in units:
            units[code] = frappe.db.get_value('Item', code, 'stock_uom')
        if units.get(code) == 'Square Meter':
            row['qty_unit'] = 'm'
            row['known_m'] = reel.get('custom_length_m') if has_length else 0
        else:
            row['known_kg'] = reel.get('declared_net_kg')
    return data


def source_labels(source_type, name):
    return add_known_quantities(_source_labels(source_type, name))


def _source_labels(source_type, name):
    import frappe
    from vcl_procurement.reel_intake import _gate, intake_labels, reel_page, _qr
    from vcl_procurement.reels import receipt_labels
    from vcl_procurement.po_reel_intake import label_data
    from urllib.parse import quote
    _gate()
    if source_type == 'Purchase Invoice':
        return intake_labels(name)
    if source_type == 'Purchase Order':
        return label_data(name)
    if source_type == 'Purchase Receipt':
        return receipt_labels(name)
    if source_type == 'Reel Intake':
        row = reel_page(name)['reel']
        if row.get('purchase_receipt') and str(row.get('notes') or '').startswith('Receipt tag preparation v1: '):
            from vcl_procurement.receipt_reel_tags import label_data as receipt_tag_labels
            return receipt_tag_labels(row['purchase_receipt'],selected=name)
        return intake_labels(row['purchase_invoice'], name=name) if row.get('purchase_invoice') else label_data(row['purchase_order'], name=name)
    if source_type == 'Batch':
        batch = frappe.get_doc('Batch', name)
        batch.check_permission('read')
        if frappe.db.exists('Reel Intake', name):
            return source_labels('Reel Intake', name)
        if batch.reference_doctype in ('Purchase Receipt','Stock Entry') and batch.reference_name:
            source = frappe.get_doc(batch.reference_doctype, batch.reference_name)
            source.check_permission('read')
            if source.docstatus == 2:
                frappe.throw('Cancelled sources cannot print active reel labels.')
        elif batch.get('custom_reel_purchase_order'):
            from vcl_procurement.po_reel_intake import source as read_po
            read_po(batch.custom_reel_purchase_order)
        else:
            frappe.throw('Open this reel through its authorized source intake or stock document.')
        if not batch.get('custom_reel_width_mm'):
            frappe.throw('This Batch has no recorded reel width. Review its reel metadata before printing.')
        item=frappe.get_doc('Item', batch.item)
        item.check_permission('read')
        return dict(receipt=name,docstatus=0,reels=[dict(batch_id=name,item_code=batch.item,item_name=item.item_name,
            supplier=batch.supplier,purchase_order=batch.get('custom_reel_purchase_order'),
            order_type=batch.get('custom_reel_order_type'),bl_number=batch.get('custom_reel_bl_number'),
            container=batch.get('custom_reel_container'),width_mm=batch.custom_reel_width_mm,gsm=batch.get('custom_reel_gsm'),
            qr_uri=_qr('/compass/reels/'+quote(name,safe='')))])
    frappe.throw('Choose a reel intake, invoice, PO, receipt or Batch source.')


def reel_identity_native(name, source_type='Purchase Invoice', format='label'):
    return native_fragment(labels_html(source_labels(source_type,name),format))
