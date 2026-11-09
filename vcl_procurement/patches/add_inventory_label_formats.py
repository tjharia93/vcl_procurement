"""Install opt-in label metadata. Never create container identities or stock in migrate."""
import frappe


def execute():
    formats = [
        ('VCL Reel Labels - 100x100', 'Purchase Invoice', '{{ reel_print_html(doc.name, "labels100") | safe }}'),
        ('VCL IC Container Labels - 100x30', 'Container Allocation', '{{ container_native_print(doc.name) | safe }}'),
        ('VCL IC Container Label - 100x30', 'Stock Container', '{{ container_native_print(doc.name, true) | safe }}'),
    ]
    for name, dt, html in formats:
        values = dict(doc_type=dt, print_format_for='DocType', standard='No', custom_format=1,
            print_format_type='Jinja', disabled=0, margin_top=0, margin_bottom=0, margin_left=0, margin_right=0,
            html=html)
        if frappe.db.exists('Print Format', name):
            doc=frappe.get_doc('Print Format', name);doc.update(values);doc.save(ignore_permissions=True)
        else:
            frappe.get_doc(dict(doctype='Print Format', name=name, **values)).insert(ignore_permissions=True)
