"""Pure rules for material requests on the phone. No frappe."""

STAGES = ("Draft", "To order", "Partly ordered", "Ordered", "Partly received", "Received", "Stopped")


def stage(row):
    """One word for where a request stands, from what ERPNext holds.

    A draft is nothing yet. Once submitted it is to order until a purchase
    order covers it, then received once the goods are in. Stopped and
    Cancelled both read Stopped: the request is over either way.
    """
    if int(row.get("docstatus") or 0) == 2 or row.get("status") in ("Stopped", "Cancelled"):
        return "Stopped"
    if int(row.get("docstatus") or 0) == 0:
        return "Draft"
    ordered = float(row.get("per_ordered") or 0)
    received = float(row.get("per_received") or 0)
    if received >= 100:
        return "Received"
    if received > 0:
        return "Partly received"
    if ordered >= 100:
        return "Ordered"
    if ordered > 0:
        return "Partly ordered"
    return "To order"


def step_index(stage_word):
    """Request -> Ordered -> Received, as a 0..2 index for a stepper; -1 when stopped."""
    return {
        "Draft": 0, "To order": 0, "Partly ordered": 1, "Ordered": 1,
        "Partly received": 2, "Received": 2,
    }.get(stage_word, -1)


def is_open(stage_word):
    return stage_word not in ("Received", "Stopped")


def clean_lines(lines):
    """(clean lines, problem) for a typed request: an item and a positive quantity per line."""
    if not isinstance(lines, list) or not lines:
        return [], "Add at least one line."
    out = []
    for position, raw in enumerate(lines, start=1):
        if not isinstance(raw, dict):
            return [], f"Line {position} is not a line."
        code = str(raw.get("item_code") or "").strip()
        if not code:
            return [], f"Line {position} names no item."
        try:
            qty = float(raw.get("qty"))
        except (TypeError, ValueError):
            return [], f"Line {position}: quantity is not a number."
        if qty <= 0:
            return [], f"Line {position}: quantity must be above zero."
        out.append({"item_code": code, "qty": qty,
                    "uom": (raw.get("uom") or None), "warehouse": (raw.get("warehouse") or None)})
    return out, None
