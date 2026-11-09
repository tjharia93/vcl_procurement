"""Checked attachment reads for the procurement documents; uploads use Frappe upload_file."""
import frappe
from vcl_procurement.api.purchasing import _assert_purchasing_role

@frappe.whitelist()
def list_files(doctype, name):
    _assert_purchasing_role()
    if doctype not in ("Purchase Order", "Purchase Receipt", "Purchase Invoice"):
        frappe.throw("Unsupported attachment document.")
    frappe.get_doc(doctype, name).check_permission("read")
    return [{"name": r.name, "file_name": r.file_name, "file_url": r.file_url, "size": r.file_size,
             "private": bool(r.is_private), "added": str(r.creation), "by": r.owner}
            for r in frappe.get_all("File", filters={"attached_to_doctype": doctype, "attached_to_name": name},
                fields=["name", "file_name", "file_url", "file_size", "is_private", "creation", "owner"],
                order_by="creation desc", limit_page_length=200)]
