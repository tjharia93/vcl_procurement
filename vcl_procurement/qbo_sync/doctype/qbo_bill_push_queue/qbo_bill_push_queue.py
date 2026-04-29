import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from vcl_procurement.api import todos


class QBOBillPushQueue(Document):
    def validate(self):
        if self.approved and self.category in ("BLOCKED", "CANCELLED"):
            frappe.throw(
                "Cannot approve a {category} row. Resolve the underlying issue first "
                "(approve the missing map, fix the source PI, or stage a fresh row), then re-tick approved.".format(
                    category=self.category
                )
            )
        if self.approved and self.category == "ALREADY_SYNCED":
            frappe.throw("ALREADY_SYNCED rows do not need approval and will not be pushed again.")
        if self.approved and self.category == "DRIFT":
            frappe.throw("DRIFT rows are flagged for review, not for push. Resolve the drift first.")

    def before_save(self):
        if not self.approved:
            self.approved_by = None
            self.approved_at = None
            return
        if not self.approved_by:
            self.approved_by = frappe.session.user
        if not self.approved_at:
            self.approved_at = now_datetime()

    def after_insert(self):
        todos.assign_queue_row(self)

    def on_update(self):
        todos.assign_queue_row(self)
