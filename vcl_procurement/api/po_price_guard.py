"""Stop a purchase order price from being recomputed behind the buyer's back.

On 4 September 2026 an order went to Labchem at 280/litre against a quote of
250 — about 3,000 too much on 100 litres. Nobody typed 280.

An ERPNext Purchase Order Item carries seven fields that all feed the price:

    price_list_rate  margin_type  margin_rate_or_amount  rate_with_margin
    discount_percentage  discount_amount  rate

When a line is priced above the price list, ERPNext does not record a new
price. It records the price list rate plus a **margin**. That margin then sits
in the document, invisible on a phone, and is re-applied on top of the base the
next time anyone edits the line. 220 + 30 became 250; then 250 + 30 became 280.

The fix is to make `rate` authoritative and remove anything that can act on it
later. `rate` is never changed here, so no total moves: this only clears the
shadow fields that would have moved it next time.

Deliberately a `validate` hook, not `before_submit` — a draft saved on a phone
today is the draft someone submits tomorrow, and it should already be clean.
"""

import frappe
from frappe.utils import flt

# Fields that silently re-derive `rate`. Zeroed once `price_list_rate` is made
# to agree with `rate`.
SHADOW_FIELDS = (
    "margin_rate_or_amount",
    "discount_percentage",
    "discount_amount",
)


def normalise_line_prices(doc, method=None):
    """Make `rate` the only thing that sets the price on every line."""
    cleaned = []

    for item in doc.get("items") or []:
        rate = flt(item.rate)
        if not rate:
            continue

        has_shadow = any(flt(item.get(f)) for f in SHADOW_FIELDS)
        base_disagrees = flt(item.price_list_rate) != rate

        if not (has_shadow or base_disagrees):
            continue

        before = {
            "price_list_rate": flt(item.price_list_rate),
            "margin_type": item.get("margin_type") or "",
            "margin_rate_or_amount": flt(item.get("margin_rate_or_amount")),
            "discount_percentage": flt(item.get("discount_percentage")),
            "discount_amount": flt(item.get("discount_amount")),
        }

        item.price_list_rate = rate
        item.base_price_list_rate = rate
        item.margin_type = ""
        item.rate_with_margin = 0
        item.base_rate_with_margin = 0
        for field in SHADOW_FIELDS:
            item.set(field, 0)

        # rate is untouched on purpose — the total the buyer agreed does not move
        item.rate = rate

        if before["margin_rate_or_amount"] or before["discount_amount"] or before["discount_percentage"]:
            cleaned.append((item.idx, item.item_code, rate, before))

    if cleaned:
        _leave_a_note(doc, cleaned)


def _leave_a_note(doc, cleaned):
    """Say what was done. A silent correction is its own kind of trap."""
    lines = [
        "Price fields normalised so the rate shown is the rate ordered.",
        "",
    ]
    for idx, item_code, rate, before in cleaned:
        carried = []
        if before["margin_rate_or_amount"]:
            carried.append(
                f"margin {before['margin_type'] or 'Amount'} "
                f"{before['margin_rate_or_amount']:g}"
            )
        if before["discount_percentage"]:
            carried.append(f"discount {before['discount_percentage']:g}%")
        if before["discount_amount"]:
            carried.append(f"discount {before['discount_amount']:g}")
        lines.append(
            f"Row {idx} · {item_code} · rate held at {rate:g}; "
            f"cleared {', '.join(carried)} "
            f"(was sitting on a base of {before['price_list_rate']:g})"
        )
    lines += [
        "",
        "Left alone, those would have been applied again on the next edit.",
    ]

    frappe.log_error(
        title=f"PO price guard · {doc.name or 'new'}",
        message="\n".join(lines),
    )

    if not doc.is_new():
        doc.add_comment("Comment", "<br>".join(lines))
