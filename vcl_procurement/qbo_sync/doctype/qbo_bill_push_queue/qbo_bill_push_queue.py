import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class QBOBillPushQueue(Document):
    def validate(self):
        if self.approved and self.category == "BLOCKED":
            frappe.throw(
                "Cannot approve a BLOCKED row. Resolve the block_reason first "
                "(e.g. approve the missing map entry, fix the source PI), then re-run staging."
            )
        if self.approved and self.category == "ALREADY_SYNCED":
            frappe.throw("ALREADY_SYNCED rows do not need approval and will not be pushed again.")

    def before_save(self):
        if not self.approved:
            self.approved_by = None
            self.approved_at = None
            return
        if not self.approved_by:
            self.approved_by = frappe.session.user
        if not self.approved_at:
            self.approved_at = now_datetime()
