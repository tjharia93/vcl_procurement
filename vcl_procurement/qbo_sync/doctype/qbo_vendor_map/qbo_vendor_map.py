import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from vcl_procurement.api import todos


class QBOVendorMap(Document):
    def before_save(self):
        _stamp_approval(self)

    def on_update(self):
        if self.approved:
            todos.close_map_todo(self.doctype, self.name)


def _stamp_approval(doc: Document) -> None:
    if not doc.approved:
        doc.approved_by = None
        doc.approved_at = None
        return
    if not doc.approved_by:
        doc.approved_by = frappe.session.user
    if not doc.approved_at:
        doc.approved_at = now_datetime()
