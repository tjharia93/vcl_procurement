import frappe
from vcl_procurement.patches.add_reel_print_formats import execute as install_formats


def execute():
    install_formats()
    old = 'VCL Reel Labels - A4 (4 pairs)'
    if frappe.db.exists('Print Format', old):
        frappe.db.set_value('Print Format', old, 'disabled', 1)
