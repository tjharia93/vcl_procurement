import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from vcl_procurement.api import todos


class QBOPurchaseTaxMap(Document):
    def autoname(self):
        rate_str = f"{float(self.erp_tax_rate or 0):.4f}"
        self.name = f"{self.erp_tax_template} | {self.erp_tax_account_head} | {rate_str}"

    def before_save(self):
        if not self.approved:
            self.approved_by = None
            self.approved_at = None
            return
        if not self.approved_by:
            self.approved_by = frappe.session.user
        if not self.approved_at:
            self.approved_at = now_datetime()

    def on_update(self):
        if self.approved:
            todos.close_map_todo(self.doctype, self.name)
