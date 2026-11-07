"""Update opt-in bundle printing metadata only; preserve every existing identity."""
import frappe
from vcl_procurement.patches.add_bundle_print_formats import execute as install_formats


def execute():
    install_formats()
    for name in ('VCL Bundle Labels - 43x58','VCL Bundle Label - 43x58'):
        if frappe.db.exists('Print Format',name):
            doc=frappe.get_doc('Print Format',name)
            if 'bundle_native_print' in (doc.html or ''):
                doc.disabled=1
                doc.save(ignore_permissions=True)
