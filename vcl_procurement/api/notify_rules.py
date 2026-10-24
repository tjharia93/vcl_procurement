"""Pure rules for the phone's notifications. No frappe.

The phone asks "what happened since I last looked"; these rules turn rows into
events with a stable id, so the phone can show each one once however often it
asks, and a title and body written for a lock screen.
"""

KINDS = ("mr_raised", "mr_submitted", "po_to_approve", "po_received")


def who(owner):
    """jane.doe@vimit.com -> Jane Doe; blank -> Someone."""
    local = (owner or "").split("@")[0].replace(".", " ").replace("_", " ").strip()
    return " ".join(w.capitalize() for w in local.split()) or "Someone"


def first_items(items, limit=2):
    """'Kraft 125gsm 2,000 Kg and 1 more' from item rows."""
    rows = [i for i in (items or []) if i.get("item_name") or i.get("item_code")]
    if not rows:
        return ""
    def one(i):
        qty = float(i.get("qty") or 0)
        q = f"{qty:,.0f}" if qty == int(qty) else f"{qty:,.2f}"
        return f"{i.get('item_name') or i.get('item_code')} {q} {i.get('uom') or ''}".strip()
    shown = ", ".join(one(i) for i in rows[:limit])
    more = len(rows) - limit
    return shown + (f" and {more} more" if more > 0 else "")


def mr_event(row, items, kind):
    name = row["name"]
    what = row.get("title") or first_items(items) or "a material request"
    if kind == "mr_raised":
        title = f"New material request {name}"
        body = f"{who(row.get('owner'))} needs {what}"
    else:
        title = f"Material request {name} is ready to order"
        body = what
    if row.get("schedule_date"):
        body += f" by {str(row['schedule_date'])[:10]}"
    return {"id": f"{kind}:{name}", "kind": kind, "doctype": "Material Request", "name": name,
            "title": title, "body": body, "at": str(row.get("at") or "")[:19]}


def po_event(row, kind):
    name = row["name"]
    sup = row.get("supplier_name") or row.get("supplier") or "a supplier"
    if kind == "po_to_approve":
        title = f"Purchase order {name} needs approval"
        body = f"{sup} · raised by {who(row.get('owner'))}"
    else:
        title = f"Goods received on {name}"
        body = f"{sup} · ready to bill"
    return {"id": f"{kind}:{name}", "kind": kind, "doctype": "Purchase Order", "name": name,
            "title": title, "body": body, "at": str(row.get("at") or "")[:19]}


def newest_first(events, limit=30):
    seen, out = set(), []
    for e in sorted(events, key=lambda e: e["at"], reverse=True):
        if e["id"] in seen:
            continue
        seen.add(e["id"])
        out.append(e)
    return out[:limit]
