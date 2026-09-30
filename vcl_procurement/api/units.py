"""Resolving a unit's conversion on the server.

One lookup used by search (`uom_factor`), by `po_save` and by
`create_purchase_order`, so a screen that sends a unit without a factor gets the
same answer everywhere instead of a hard error. The order of trust is in
`rules.uom_factor`.
"""

import frappe

from vcl_procurement.api import rules


def resolve_factor(item_code, uom):
    """(stock_uom, factor). factor is None when neither the item nor the site table knows."""
    if not frappe.db.exists("Item", item_code):
        frappe.throw(f"Item {item_code} was not found.")
    item = frappe.get_doc("Item", item_code)
    stock = item.stock_uom
    own = [{"uom": u.uom, "conversion_factor": u.conversion_factor} for u in item.get("uoms") or []]

    def _g(f, t):
        v = frappe.db.get_value("UOM Conversion Factor", {"from_uom": f, "to_uom": t}, "value")
        return float(v) if v else None

    return stock, rules.uom_factor(uom, stock, own, _g(uom, stock), _g(stock, uom))
