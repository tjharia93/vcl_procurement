"""/scan: look up a scanned VCL label. Signed-in users only; results follow the site's permissions."""
import frappe

no_cache = 1


def get_context(context):
    if frappe.session.user == 'Guest':
        frappe.local.flags.redirect_location = '/login?redirect-to=/scan'
        raise frappe.Redirect
    context.csrf_token = frappe.sessions.get_csrf_token()
    context.no_cache = 1
    return context
