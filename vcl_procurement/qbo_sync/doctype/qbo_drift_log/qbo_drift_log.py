import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class QBODriftLog(Document):
    def before_save(self):
        if not self.resolved:
            self.resolved_by = None
            self.resolved_at = None
            return
        if not self.resolved_by:
            self.resolved_by = frappe.session.user
        if not self.resolved_at:
            self.resolved_at = now_datetime()
