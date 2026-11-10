"""Shared browser/native bundle labels. Formats carry no purchasing prices or stock balance."""
from html import escape
from vcl_procurement.print_brand import brand_icon, label_brand_css


def labels_html(rows, format='thermal'):
    if not rows:
        raise ValueError('No registered bundles match this print selection.')
    if format not in ('thermal','a4'):
        raise ValueError('Choose 100 x 30 mm thermal labels or A4.')
    thermal=format=='thermal'
    width,height=(100,30) if thermal else (210,297)
    page_rule='100mm 30mm' if thermal else 'A4 portrait'
    def e(v): return escape(str(v if v is not None else ''),quote=True)
    labels=[]
    for r in rows:
        status='ON HOLD' if r['status']=='On hold' else 'COUNT CHECKED' if r['status']=='Checked' else 'REGISTERED'
        labels.append(f'''<section class="bundle-label">
            <div class="brand">{brand_icon()}VIMIT CONVERTERS | BUNDLE</div><h1>{e(r['name'])}</h1>
            <h2>{e(r['material'])} {e(r['gsm'])} GSM</h2><div class="spec">{e(r['colour'].upper())} | {e(r['width_mm'])} x {e(r['length_mm'])} mm</div>
            <div class="contents">{e(r['reams_per_bundle'])} REAMS / {e(r['total_sheets'])} SHEETS</div>
            <div class="packing">{e(r['sheets_per_ream'])} sheets per ream</div>
            <div class="check"><div class="writing"><span>Counted by</span><div class="line">{e(r.get('verified_by') or '')}</div><span>Date</span><div class="line">{e(str(r.get('verified_on') or '')[:10])}</div></div>
            <img alt="Open bundle record" src="{e(r['qr_uri'])}"></div>
            <footer><b>{status} - STOCK NOT POSTED</b><div>Scan for physical custody history</div></footer></section>''')
    if thermal:
        pages=''.join('<article class="bundle-sheet">'+label+'</article>' for label in labels)
    else:
        pages=''.join('<article class="bundle-sheet">'+''.join(labels[i:i+8])+'</article>' for i in range(0,len(labels),8))
    return f'''<!doctype html><html><head><meta charset="utf-8"><title>Bundle labels</title><style>
    @page{{size:{page_rule};margin:0}}*{{box-sizing:border-box}}html,body{{margin:0;padding:0;font-family:Arial,sans-serif;color:#000}}
    .print-format{{page-size:{'Custom' if thermal else 'A4'};page-width:{width}mm;page-height:{height}mm;orientation:portrait;margin-top:0;margin-bottom:0;margin-left:0;margin-right:0;padding:0!important;width:{width}mm!important;max-width:none!important}}
    .bundle-sheet{{width:{width}mm;height:{height}mm;page-break-after:always;page-break-inside:avoid;{'padding:5mm;font-size:0' if not thermal else ''}}}
    .bundle-sheet:last-child{{page-break-after:auto}}
    .bundle-label{{width:43mm;height:58mm;padding:2.5mm;position:relative;overflow-wrap:anywhere;font-family:Arial,sans-serif;line-height:1.15;color:#000}}
    .brand{{font-size:6.2pt;font-weight:bold;white-space:nowrap}}.bundle-label h1{{font-size:8.7pt;font-weight:bold;margin:1.4mm 0 1.5mm;line-height:1.15;white-space:nowrap}}
    .bundle-label h2{{font-size:10.5pt;font-weight:bold;margin:0 0 1.5mm;line-height:1.1;max-height:8mm;overflow:hidden}}
    .spec{{font-size:8.5pt;font-weight:bold;margin-bottom:1.5mm;line-height:1.15}}
    .contents{{font-size:9pt;font-weight:bold;white-space:nowrap}}.packing{{font-size:7.5pt;margin-top:.7mm}}
    .check{{margin-top:1.7mm;height:19mm;position:relative}}.writing{{width:17.5mm;font-size:6.5pt}}
    .line{{border-bottom:.15mm solid #333;height:5.5mm;font-size:5pt;margin-bottom:1.3mm;overflow:hidden}}
    .check img{{position:absolute;right:0;top:0;width:19mm;height:19mm}}
    .bundle-label footer{{position:absolute;left:2.5mm;right:2.5mm;bottom:2mm;font-size:5.4pt;line-height:1.4}}
    {'' if thermal else '.bundle-label{display:inline-block;vertical-align:top;width:100mm;height:71.75mm;border-top:.2mm solid #555;border-left:.2mm solid #555;padding:4mm;font-size:8pt}.bundle-label:nth-child(even){border-right:.2mm solid #555}.bundle-label:nth-last-child(-n+2){border-bottom:.2mm solid #555}.brand{font-size:8pt}.bundle-label h1{font-size:13pt;margin:2mm 0}.bundle-label h2{font-size:13pt;max-height:8mm}.spec{font-size:11pt}.contents{font-size:12pt}.packing{font-size:9pt}.check{height:23mm;margin-top:2mm}.check img{width:23mm;height:23mm}.writing{width:55mm;font-size:8pt}.line{font-size:7pt}.bundle-label footer{left:4mm;right:4mm;bottom:2mm;font-size:6pt}'}
    {''' .bundle-label{width:100mm;height:30mm;padding:0}
    .brand{position:absolute;left:2.5mm;top:1.5mm;font-size:6pt}
    .bundle-label h1{position:absolute;left:2.5mm;top:4.5mm;width:63mm;margin:0;font-size:11pt;line-height:1.1}
    .bundle-label h2{position:absolute;left:2.5mm;top:9mm;width:63mm;margin:0;font-size:10.5pt;max-height:4.5mm;line-height:1.1}
    .spec{position:absolute;left:2.5mm;top:13.5mm;width:63mm;margin:0;font-size:9pt;line-height:1.1}
    .contents{position:absolute;left:2.5mm;top:18mm;width:40mm;font-size:9.5pt;line-height:1.1}
    .packing{position:absolute;left:44mm;top:18.3mm;width:22mm;margin:0;font-size:6.5pt;line-height:1.1}
    .check{position:static;height:0;margin:0}.check img{top:3mm;right:7mm;width:24mm;height:24mm}
    .writing{position:absolute;left:2.5mm;top:22mm;width:63mm;font-size:6.5pt;white-space:nowrap}
    .writing span{display:inline-block;width:13mm;vertical-align:bottom}.writing span:nth-of-type(2){width:7mm;margin-left:1mm}
    .line{display:inline-block;width:20mm;height:3.5mm;margin:0;vertical-align:bottom;font-size:5.5pt;white-space:nowrap}
    .bundle-label footer{left:2.5mm;right:auto;width:63mm;bottom:1mm;font-size:5.4pt;line-height:1.1}
    .bundle-label footer div{display:none}
    ''' if thermal else ''}
    .tools{{padding:12px;text-align:center}}.tools button{{padding:8px 16px;margin:0 8px}}
    @media print{{.tools{{display:none}}.bundle-sheet{{margin:0}}}}
    {label_brand_css()}</style></head><body><div class="tools">Print at 100% / Actual size. Disable headers and footers.<button onclick="window.print()">Print</button><button onclick="window.close()">Close</button></div>{pages}</body></html>'''
