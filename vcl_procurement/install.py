import frappe


def after_install():
    _ensure_settings_doc()
    frappe.db.commit()


def _ensure_settings_doc() -> None:
    """Create the QBO Sync Settings Single with safe defaults if missing.

    `staging_enabled` defaults to 0 — admin must explicitly turn it on once
    the cutoff date is confirmed. All submitted PIs on/after the cutoff are
    eligible (no Local/Import filter).
    """
    if frappe.db.exists("DocType", "QBO Sync Settings"):
        doc = frappe.get_single("QBO Sync Settings")
        changed = False
        if not doc.earliest_posting_date:
            doc.earliest_posting_date = "2026-04-01"
            changed = True
        if not doc.assignee_user and frappe.db.exists("User", "tanuj.haria@vimit.com"):
            doc.assignee_user = "tanuj.haria@vimit.com"
            changed = True
        if not doc.auto_push_polling_minutes:
            doc.auto_push_polling_minutes = 5
            changed = True
        if changed:
            doc.save(ignore_permissions=True)
