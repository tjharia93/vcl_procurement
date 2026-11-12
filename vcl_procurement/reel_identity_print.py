"""Standard identity-only 100 x 70 mm reel labels, independent of stock UOM.

Every print path reads authorized source records and reuses their permanent IDs.
No quantity, receiving field, stock mutation or ID allocation belongs here.
"""
from html import escape
import re


def e(value):
    return escape(str(value or ''), quote=True)


def n(value):
    return f'{float(value):g}'


def material_name(row):
    text = str(row.get('item_name') or row.get('item_code') or '')
    text = re.split(r'\s*-\s*Reel(?:\s|-)', text, maxsplit=1, flags=re.I)[0]
    if ' - ' in text and text.split(' - ',1)[0].strip() in str(row.get('item_code') or ''):
        text = text.split(' - ', 1)[1]
    text = re.sub(r'\s*-\s*\d+(?:\.\d+)?\s*mm\s*$', '', text, flags=re.I)
    if row.get('gsm'):
        text = re.sub(r'\b\d+(?:\.\d+)?\s*gsm\b', '', text, flags=re.I)
    text = re.sub(r'\(\s*Yellow\s+Back\s*\)', '', text, flags=re.I)
    return ' '.join(text.split())


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
        gsm = f"<span class='gsm'><i>|</i>{n(r['gsm'])} GSM</span>" if r.get('gsm') else ''
        backing = ' | Yellow back' if 'yellow back' in (r.get('item_name') or '').lower() else ''
        refs = [('SUPPLIER', r.get('supplier') or '—')]
        if r.get('order_type') == 'Import' and r.get('bl_number'):
            refs.append(('BL', r['bl_number']))
            if r.get('container'):
                refs.append(('CONTAINER', r['container']))
        elif r.get('purchase_order'):
            refs.append(('PO', r['purchase_order']))
        elif r.get('existing_stock'):
            refs.append(('SOURCE', 'Existing stock'))
        reference_html = ''.join(f'<div><span>{key}</span><b>{e(value)}</b></div>' for key,value in refs)
        title_size = 13 if len(material_name(r)) <= 18 else 10 if len(material_name(r)) <= 30 else 8
        cards.append(f'''<section class="po-reel-label">
        <div class="brand">VIMIT CONVERTERS LIMITED</div>
        <h1>{e(r['batch_id'])}</h1><h2 style="font-size:{title_size}pt">{e(material_name(r))}</h2>
        <div class="material-code">{e(r.get('item_code'))}{backing}</div>
        <div class="width-caption">REEL WIDTH</div>
        <div class="spec"><strong>{n(r['width_mm']) if r.get('width_mm') else '—'} <small>mm</small></strong>{gsm}</div>
        <img class="qr" alt="Open reel record" src="{e(r['qr_uri'])}">
        <div class="scan-caption">SCAN REEL RECORD</div>
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
    .po-reel-label .refs span{{width:13mm;flex:none;font-size:5.5pt;font-weight:bold}}
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


def source_labels(source_type, name):
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

