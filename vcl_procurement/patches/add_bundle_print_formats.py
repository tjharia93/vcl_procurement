"""Install bundle labels only; never seed identities or post stock during migrate."""
import frappe


def execute():
    for name,dt,single in (
        ('VCL Bundle Labels - 100x30','Bundle Registration',False),
        ('VCL Bundle Label - 100x30','Stock Bundle',True)):
        values=dict(doc_type=dt,print_format_for='DocType',standard='No',custom_format=1,
            print_format_type='Jinja',disabled=0,margin_top=0,margin_bottom=0,margin_left=0,margin_right=0,
            html='{{ bundle_native_print(doc.name, '+str(single).lower()+') | safe }}')
        if frappe.db.exists('Print Format',name):
            doc=frappe.get_doc('Print Format',name);doc.update(values);doc.save(ignore_permissions=True)
        else:
            frappe.get_doc(dict(doctype='Print Format',name=name,**values)).insert(ignore_permissions=True)
