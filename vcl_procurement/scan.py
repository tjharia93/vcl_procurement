"""Scan look-up for the /scan page: a scanned ID becomes the record it names.

Read only. A unit is shown only when the user may read its record under the site's own
role and user permissions. An unknown ID and one the user may not see get the same answer,
so the screen cannot be used to test which IDs exist. Every look-up is logged and each user
is limited to LIMIT look-ups a minute.
"""
import frappe
from frappe.utils import now_datetime
from vcl_procurement import scan_core as core

LIMIT = 120


def _cache():
    return frappe.cache() if callable(frappe.cache) else frappe.cache


def _rate_limit(user):
    key = f"vcl_scan:{getattr(frappe.local, 'site', '')}:{user}:{now_datetime().strftime('%Y%m%d%H%M')}"
    cache = _cache()
    count = cache.incrby(key, 1)
    if count == 1:
        cache.expire(key, 120)
    if count > LIMIT:
        frappe.throw('Too many look-ups. Wait a minute and scan again.', frappe.RateLimitExceededError)


def _find(uid, prefix):
    if prefix == 'LOC':
        return dict(found=False, location=uid,
                    message='This is a location label. Put-away and counts by location are not built yet.')
    for doctype, fields, shape in core.SOURCES.get(prefix, []):
        if not frappe.db.exists('DocType', doctype) or not frappe.db.exists(doctype, uid):
            continue
        if not frappe.has_permission(doctype, 'read', doc=uid):
            continue
        row = frappe.db.get_value(doctype, uid, [f for f in fields if f == 'name' or frappe.get_meta(doctype).has_field(f)],
                                  as_dict=True)
        if row:
            return shape(row)
    return None


@frappe.whitelist(methods=['POST'])
def resolve(code=None):
    user = frappe.session.user
    if not user or user == 'Guest':
        frappe.throw('Sign in to scan.', frappe.PermissionError)
    _rate_limit(user)
    parsed = core.normalize(code)
    result = _find(*parsed) if parsed else None
    frappe.logger('vcl_scan').info(dict(user=user, code=str(code or '')[:80], id=parsed[0] if parsed else None,
                                        found=bool(result and result.get('found'))))
    return result or dict(core.NOT_FOUND)
