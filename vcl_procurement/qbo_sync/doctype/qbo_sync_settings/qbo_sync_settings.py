import frappe
from frappe.model.document import Document


class QBOSyncSettings(Document):
    def validate(self):
        if not (self.import_or_local_fieldname or "").strip():
            frappe.throw("Import/Local Fieldname is required.")
        if not (self.import_or_local_local_value or "").strip():
            frappe.throw("Local Value is required.")
        if not self.earliest_posting_date:
            frappe.throw("Earliest Posting Date is required.")
        if not self.assignee_user:
            frappe.throw("Assignee User is required.")
        if self.auto_push_polling_minutes and self.auto_push_polling_minutes < 1:
            frappe.throw("Runner Push Poll Interval must be at least 1 minute.")


def get_settings() -> Document:
    return frappe.get_cached_doc("QBO Sync Settings")
