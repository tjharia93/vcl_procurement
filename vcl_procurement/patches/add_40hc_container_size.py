"""Offer "40 HC" (40 ft high cube) as a container size on a purchase invoice's containers (def-pc1027-07).

`container_size` on the Container Shipping Details child table was set up by hand with 20 FT and
40 FT. Idempotent: adds the option only when the field is a Select that lacks it, and never
removes or reorders an existing one.
"""

import frappe

CHILD = "Container Shipping Details"
FIELD = "container_size"
NEW = "40 HC"


def with_option(options, new=NEW):
    """The options text with `new` added at the end, or None when it is already there."""
    text = str(options or "")
    if new in [o.strip() for o in text.split("\n")]:
        return None
    return text.rstrip("\n") + "\n" + new if text.strip() else new


def execute():
    if not frappe.db.exists("DocType", CHILD):
        return
    cf = frappe.db.get_value("Custom Field", {"dt": CHILD, "fieldname": FIELD}, ["name", "fieldtype", "options"], as_dict=True)
    if cf:
        if cf.fieldtype == "Select" and with_option(cf.options):
            frappe.db.set_value("Custom Field", cf.name, "options", with_option(cf.options))
            frappe.clear_cache(doctype=CHILD)
        return
    df = frappe.get_meta(CHILD).get_field(FIELD)       # effective options, any earlier property setter included
    if df and df.fieldtype == "Select" and with_option(df.options):
        from frappe.custom.doctype.property_setter.property_setter import make_property_setter
        make_property_setter(CHILD, FIELD, "options", with_option(df.options), "Text", validate_fields_for_doctype=False)
        frappe.clear_cache(doctype=CHILD)
