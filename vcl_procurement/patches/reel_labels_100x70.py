"""Install the shared identity-only reel formats; never allocate IDs or post stock."""
import frappe

FORMATS={
    'VCL Reel Labels - 100x70':('Purchase Invoice','label'),
    'VCL PO Reel Labels - 100x70':('Purchase Order','label'),
    'VCL Receipt Reel Labels - 100x70':('Purchase Receipt','label'),
    'VCL Reel Label - 100x70':('Reel Intake','label'),
    'VCL Batch Reel Label - 100x70':('Batch','label'),
    'VCL Reel Labels - A4 (8 labels)':('Purchase Invoice','a4'),
    'VCL PO Reel Labels - A4 (8 labels)':('Purchase Order','a4'),
    'VCL Receipt Reel Labels - A4 (8 labels)':('Purchase Receipt','a4'),
}


def execute():
    for name,(doctype,format) in FORMATS.items():
        values=dict(doc_type=doctype,print_format_for='DocType',standard='No',custom_format=1,
            print_format_type='Jinja',disabled=0,margin_top=0,margin_bottom=0,margin_left=0,margin_right=0,
            html='{{ reel_identity_native(doc.name, "'+doctype+'", "'+format+'") | safe }}')
        if frappe.db.exists('Print Format',name):
            doc=frappe.get_doc('Print Format',name);doc.update(values);doc.save(ignore_permissions=True)
        else:
            frappe.get_doc(dict(doctype='Print Format',name=name,**values)).insert(ignore_permissions=True)
    for name in ('VCL Reel Labels - 100x100','VCL Reel Labels - A4 (6 labels)','VCL Reel Labels - A4 (4 pairs)'):
        if frappe.db.exists('Print Format',name):
            doc=frappe.get_doc('Print Format',name)
            if 'reel_print_html' in (doc.html or ''):
                doc.disabled=1;doc.save(ignore_permissions=True)
