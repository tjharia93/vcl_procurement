app_name = "vcl_procurement"
app_title = "VCL Procurement"
app_publisher = "VCL"
app_description = "VCL Procurement App - Phase 1: Purchase Invoice sync from ERPNext to QuickBooks Online via per-line Item/Account routing, with human approval queue and drift detection."
app_email = "info@vcl.co.tz"
app_license = "MIT"
required_apps = ["frappe", "erpnext"]

# Document Events
# ----------------
doc_events = {
    "Purchase Invoice": {
        "on_submit": "vcl_procurement.api.staging.on_submit_purchase_invoice",
        "on_cancel": "vcl_procurement.api.staging.on_cancel_purchase_invoice",
    },
    # A margin left on a PO line is re-applied on top of the base the next time
    # anyone edits it, which is how Labchem was ordered at 280 against a quote
    # of 250 on 4 Sep 2026. `rate` is made authoritative on every save.
    "Purchase Order": {
        "validate": "vcl_procurement.api.po_price_guard.normalise_line_prices",
    },
}

# Fixtures
# --------
fixtures = [
    {
        "doctype": "Custom Field",
        "filters": [
            ["dt", "=", "Purchase Invoice"],
            [
                "fieldname",
                "in",
                [
                    "custom_qbo_bill_id",
                    "custom_qbo_synced_at",
                    "custom_qbo_sync_status",
                ],
            ],
        ],
    }
]

# Installation
# ------------
after_install = "vcl_procurement.install.after_install"

# Scheduled Tasks
# ----------------
scheduler_events = {}
