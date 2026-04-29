import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class QBOVendorMap(Document):
    def before_save(self):
        _stamp_approval(self)


def _stamp_approval(doc: Document) -> None:
    if not doc.approved:
        doc.approved_by = None
        doc.approved_at = None
        return
    if not doc.approved_by:
        doc.approved_by = frappe.session.user
    if not doc.approved_at:
        doc.approved_at = now_datetime()
