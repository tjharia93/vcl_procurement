"""Authorized, read-only label preview for every reel printing screen."""
import json
import frappe
from vcl_procurement.reel_identity_print import labels_html, source_labels


@frappe.whitelist()
def preview(source_type, name, format='label', names=None):
    data=source_labels(source_type,name)
    if names is not None:
        try:
            selected=json.loads(names) if isinstance(names,str) else names
        except (ValueError,TypeError):
            frappe.throw('Select distinct registered reel identities.')
        if not isinstance(selected,list) or not selected or len(selected)>5000 or not all(isinstance(n,str) for n in selected) or len(set(selected))!=len(selected):
            frappe.throw('Select distinct registered reel identities (maximum 5000).')
        allowed={r['batch_id'] for r in data['reels']}
        if not set(selected).issubset(allowed):
            frappe.throw('Every selected reel must belong to the authorized print source.')
        data={**data,'reels':[r for r in data['reels'] if r['batch_id'] in set(selected)]}
    try:
        return dict(html=labels_html(data,format))
    except ValueError as error:
        frappe.throw(str(error))
