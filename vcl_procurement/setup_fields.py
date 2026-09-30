"""Custom Fields the purchasing screens depend on.

Created live on 29 Sep 2026 while the screens were prototyped at /purchasing-dev.
This module makes them reproducible: the patch and after_install both call
`ensure_fields()`, which is idempotent and never touches an existing field.

Both are needed because installing an app stamps every patch as done WITHOUT
running it (frappe/installer.py set_all_patches_as_completed), so a patch alone
would leave a fresh site without the fields.
"""

import frappe

FIELDS = [
    {
        "dt": "Purchase Invoice",
        "fieldname": "custom_kra_entry_date",
        "label": "KRA Customs Entry Date",
        "fieldtype": "Date",
        "insert_after": "custom_kra_import_number",
        "description": ("Date of the KRA customs entry. Used for VAT, so it is also the ledger "
                        "posting date. Payment due date still runs from the supplier invoice date."),
    },
    {
        "dt": "Purchase Invoice",
        "fieldname": "custom_file_number",
        "label": "File Number",
        "fieldtype": "Data",
        "read_only": 1,
        "insert_after": "custom_proforma_invoice_number_importation_link",
        "description": "Filled from the linked Purchase Order. Not edited here.",
    },
]


def ensure_fields():
    """Create any missing field. Returns the fieldnames it created."""
    made = []
    for spec in FIELDS:
        if frappe.db.exists("Custom Field", {"dt": spec["dt"], "fieldname": spec["fieldname"]}):
            continue
        frappe.get_doc({"doctype": "Custom Field", **spec}).insert(ignore_permissions=True)
        made.append(spec["fieldname"])
    return made
