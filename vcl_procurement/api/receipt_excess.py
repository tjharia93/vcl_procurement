"""Manager-approved PO quantity increases for an actual excess delivery.

Native ERPNext Update Items owns the write, workflow and authorization checks.
Approval and its PO audit comment commit together. No stock is posted here.
"""
import hashlib
from html import escape
import json
import re

import frappe
from frappe.utils import getdate, nowdate
from vcl_procurement.api.purchasing import _assert_purchasing_role, _receipt_limits, receipt_preview
from vcl_procurement.api import receipt_excess_rules as rules

MANAGERS = {'Purchase Manager', 'System Manager'}


def _order(po):
    _assert_purchasing_role()
    doc = frappe.get_doc('Purchase Order', po)
    doc.check_permission('read')
    if doc.docstatus != 1 or doc.status in ('Closed', 'Cancelled', 'On Hold', 'Completed', 'Delivered'):
        frappe.throw('Approve excess only against an open, submitted Purchase Order.')
    return doc


def _plan(doc, lines):
    if isinstance(lines, str):
        try:
            lines = json.loads(lines)
        except ValueError:
            frappe.throw('Enter the actual receiving quantities.')
    _global, limits = _receipt_limits(doc)
    try:
        return rules.proposal([r.as_dict() for r in doc.items], lines, limits)
    except ValueError as error:
        frappe.throw(str(error))


@frappe.whitelist()
def preview(po, lines):
    doc = _order(po)
    wanted, changes = _plan(doc, lines)
    return dict(po=doc.name, expected_modified=str(doc.modified), currency=doc.currency,
        supplier=doc.supplier, lines=changes, requested=wanted,
        additional_net=round(sum(r['extra_net'] for r in changes), 6),
        current_grand_total=doc.grand_total,
        can_approve=bool(MANAGERS.intersection(frappe.get_roles())) and doc.has_permission('write'))


def _native_update(po, items):
    from erpnext.controllers.accounts_controller import update_child_qty_rate
    update_child_qty_rate(parent_doctype='Purchase Order', parent_doctype_name=po,
                          trans_items=json.dumps(items), child_docname='items')


@frappe.whitelist(methods=['POST'])
def approve(po, lines, expected_modified, reason, approval_id, posting_date=None, supplier_delivery_note=None):
    _assert_purchasing_role()
    if not MANAGERS.intersection(frappe.get_roles()):
        frappe.throw('A Purchase Manager must approve the additional PO quantity.', frappe.PermissionError)
    if not isinstance(approval_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,64}', approval_id):
        frappe.throw('Invalid excess-delivery approval reference.')
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 1000:
        frappe.throw('Record the reason for accepting this excess delivery (up to 1000 characters).')
    if not expected_modified:
        frappe.throw('Reload the excess-delivery review before approving.')
    if posting_date:
        try:
            if getdate(posting_date) > getdate(nowdate()):
                raise ValueError()
        except (ValueError, TypeError):
            frappe.throw('Choose an actual receiving date no later than today.')
    note = str(supplier_delivery_note or '').strip()
    if len(note)>140:
        frappe.throw('The delivery-note number must fit within 140 characters.')
    doc = _order(po)
    doc.check_permission('write')
    # Serialize approvals before comparing the reviewed version or retry key.
    frappe.db.sql('select name from `tabPurchase Order` where name=%s for update', (doc.name,))
    doc.reload()
    doc = _order(po)
    doc.check_permission('write')
    wanted, changes = _plan(doc, lines)
    fingerprint = hashlib.sha256(json.dumps(dict(po=po,lines=wanted,expected_modified=expected_modified,
        reason=reason.strip(),posting_date=str(posting_date or ''),supplier_delivery_note=note),sort_keys=True).encode()).hexdigest()
    marker = 'Receiving excess approval: '+approval_id+';'
    previous = frappe.get_all('Comment', filters={'reference_doctype':'Purchase Order',
        'reference_name':po,'comment_type':'Comment','content':['like','%'+marker+'%']},
        fields=['name','content'],limit_page_length=2)
    if previous:
        if len(previous)!=1 or 'Fingerprint: '+fingerprint not in previous[0].content:
            frappe.throw('This approval reference has different details. Reopen the review.')
        if changes:
            frappe.throw('The PO quantities changed after this approval. Reopen the review.')
        return dict(approved=True,reused=True,comment=previous[0].name,stock_posted=False,
                    receipt_preview=receipt_preview(po))
    if str(doc.modified) != str(expected_modified):
        frappe.throw('This Purchase Order changed after the review. Reopen the review before approving.')
    if not changes:
        frappe.throw('The quantities are already within the receiving limit. Reload before continuing.')
    payload = rules.native_items([r.as_dict() for r in doc.items], changes)
    # This native operation checks write permission, workflow, budget and approving
    # authority, retains the PO number and recalculates taxes/payment schedules.
    _native_update(po, payload)
    fresh = _order(po)
    by_name = {r.name:r for r in fresh.items}
    if any(abs(float(by_name[r['po_detail']].qty)-r['new_order_qty'])>1e-6 for r in changes):
        frappe.throw('ERPNext did not retain the reviewed quantity increase. Nothing is approved.')
    e=lambda v:escape(str(v),quote=True)
    summary=''.join('<li>PO line '+str(r['idx'])+': '+e(r['ordered'])+' → '+e(r['new_order_qty'])+' '+e(r['uom'])+
        '; received this delivery '+e(r['receive_now'])+'; existing rate '+e(r['rate'])+' '+e(doc.currency)+'</li>' for r in changes)
    content=('<p>'+marker+'</p><p>Fingerprint: '+fingerprint+'</p><p>Reason: '+e(reason.strip())+'</p>'+
        '<p>Delivery note: '+e(note or 'Not provided')+'; receiving date: '+e(posting_date or 'Not provided')+'</p><ul>'+summary+'</ul>'+
        '<p>Additional value before tax: '+e(round(sum(r['extra_net'] for r in changes),6))+' '+e(doc.currency)+
        '. PO quantity approved; receipt submission is a separate step.</p>')
    comment = fresh.add_comment('Comment', text=content)
    return dict(approved=True,reused=False,comment=comment.name,stock_posted=False,
                receipt_preview=receipt_preview(po))
