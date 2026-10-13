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
    {
        "dt": "Purchase Invoice",
        "fieldname": "custom_acd_reference_code",
        "label": "ACD Reference Code",
        "fieldtype": "Data",
        "allow_on_submit": 1,
        "insert_after": "custom_number_of_days_free",
        "description": ("Advance Cargo Declaration reference. Required before an Importation invoice is "
                        "submitted; chased by email every Monday until the container lands."),
    },
    {
        "dt": "Purchase Invoice",
        "fieldname": "custom_export_entry_received",
        "label": "Export Entry Received",
        "fieldtype": "Check",
        "allow_on_submit": 1,
        "insert_after": "custom_acd_reference_code",
        "description": "Ticked when the supplier's export entry has been received.",
    },
]

# Custom Fields on the QBO Bill Push Queue: send an invoice to QuickBooks as an Expense (paid by
# cheque) instead of a Bill, and post a typed VAT amount as its own line with no rate calculation.
_Q = "QBO Bill Push Queue"
FIELDS += [
    {"dt": _Q, "fieldname": "qbo_txn_type", "label": "Send to QuickBooks as", "fieldtype": "Select",
     "options": "Bill\nExpense", "default": "Bill", "allow_on_submit": 1, "insert_after": "qbo_memo"},
    {"dt": _Q, "fieldname": "pay_bank_account", "label": "Paying bank account (QBO id)", "fieldtype": "Data", "allow_on_submit": 1, "insert_after": "qbo_txn_type"},
    {"dt": _Q, "fieldname": "pay_bank_name", "label": "Paying bank account", "fieldtype": "Data", "allow_on_submit": 1, "insert_after": "pay_bank_account"},
    {"dt": _Q, "fieldname": "pay_cheque_no", "label": "Cheque number", "fieldtype": "Data", "allow_on_submit": 1, "insert_after": "pay_bank_name"},
    {"dt": _Q, "fieldname": "pay_amount", "label": "Cheque amount (KES)", "fieldtype": "Currency", "allow_on_submit": 1, "insert_after": "pay_cheque_no"},
    {"dt": _Q, "fieldname": "pay_date", "label": "Cheque date", "fieldtype": "Date", "allow_on_submit": 1, "insert_after": "pay_amount"},
    {"dt": _Q, "fieldname": "vat_line_manual", "label": "VAT as its own line (typed amount)", "fieldtype": "Check", "allow_on_submit": 1, "insert_after": "pay_date"},
    {"dt": _Q, "fieldname": "vat_line_account", "label": "VAT account (QBO id)", "fieldtype": "Data", "allow_on_submit": 1, "insert_after": "vat_line_manual"},
    {"dt": _Q, "fieldname": "vat_line_account_name", "label": "VAT account", "fieldtype": "Data", "allow_on_submit": 1, "insert_after": "vat_line_account"},
    {"dt": _Q, "fieldname": "vat_line_amount", "label": "VAT amount (KES)", "fieldtype": "Currency", "allow_on_submit": 1, "insert_after": "vat_line_account_name"},
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
