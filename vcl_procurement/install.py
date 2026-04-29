import frappe


def after_install():
    frappe.db.commit()
