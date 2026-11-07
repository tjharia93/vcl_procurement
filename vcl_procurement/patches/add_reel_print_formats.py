"""Install two opt-in Purchase Invoice formats; leave invoice printing defaults alone."""
import frappe

FORMATS = {
    'VCL Reel Labels - A4 (4 pairs)': 'labels',
    'VCL Full Reel Receiving List': 'receiving',
}


def execute():
    for name, kind in FORMATS.items():
        # These named formats are app-owned. Never change other formats/defaults.
        values = dict(doc_type='Purchase Invoice', print_format_for='DocType',
            standard='No', custom_format=1, print_format_type='Jinja', disabled=0,
            margin_top=0, margin_bottom=0, margin_left=0, margin_right=0,
            html='{{ reel_print_html(doc.name, "' + kind + '") | safe }}')
        if frappe.db.exists('Print Format', name):
            doc = frappe.get_doc('Print Format', name)
            doc.update(values)
            doc.save(ignore_permissions=True)
        else:
            frappe.get_doc(dict(doctype='Print Format', name=name, **values)).insert(ignore_permissions=True)
