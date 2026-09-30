#!/usr/bin/env python3
"""Post-deploy check of the purchasing APIs against the live site. READ-ONLY.

    python3 scripts/smoke_purchasing.py                # uses /opt/vcl/CommandCentre/config/settings.toml [erpnext]
    SMOKE_PO=PUR-ORD-2026-00237 SMOKE_PI=ACC-PINV-2026-00594 python3 scripts/smoke_purchasing.py

It calls only reads. It never saves, submits or pays anything. Run it right after
deploy + migrate and paste the output: a failing line names the API that is wrong.
"""
import json
import os
import sys
import tomllib
import urllib.parse
import urllib.request

CFG = tomllib.load(open(os.environ.get("SMOKE_SETTINGS", "/opt/vcl/CommandCentre/config/settings.toml"), "rb"))["erpnext"]
BASE = CFG["base_url"].rstrip("/")
HDR = {"Authorization": f"token {CFG['api_key']}:{CFG['api_secret']}"}
PO = os.environ.get("SMOKE_PO", "PUR-ORD-2026-00237")
PI = os.environ.get("SMOKE_PI", "ACC-PINV-2026-00594")
P = "vcl_procurement.api."
PRICE_FIELDS = {"price_list_rate", "margin_type", "margin_rate_or_amount", "rate_with_margin",
                "discount_percentage", "discount_amount", "base_price_list_rate"}
fails = []


def call(method, **args):
    url = f"{BASE}/api/method/{P}{method}?" + urllib.parse.urlencode(
        {k: (v if isinstance(v, str) else json.dumps(v)) for k, v in args.items()})
    with urllib.request.urlopen(urllib.request.Request(url, headers=HDR), timeout=60) as r:
        return json.load(r)["message"]


def check(label, fn):
    try:
        note = fn()
        print(f"  ok    {label}" + (f"  ({note})" if note else ""))
    except Exception as e:  # noqa: BLE001
        fails.append(label)
        print(f"  FAIL  {label}: {str(e)[:160]}")


def _search_items():
    r = call("search.search_items", q="ncr%cfb%whi")
    assert r, "no results for 'ncr%cfb%whi'"
    r2 = call("search.search_items", q="whi ncr cb")
    assert any(x["item_code"] == "NCR-250-55-WHI-CB" for x in r2), "NCR-250-55-WHI-CB not found by 'whi ncr cb'"
    return f"{len(r)} results, any order works"


def _uom():
    f = call("search.uom_factor", item_code="NCR-250-55-WHI-CB", uom="Tonne")
    assert f["factor"] == 1000, f"Tonne factor {f}"
    assert call("search.uom_factor", item_code="NCR-250-55-WHI-CB", uom="Kg")["factor"] == 1
    assert len(call("search.uom_list")) > 100
    return "Tonne = 1000 Kg"


def _open_orders():
    r = call("purchasing.open_orders")
    row = r["rows"][0]
    for k in ("order_type", "confirmation_no", "file_no", "connections", "total", "currency"):
        assert k in row, f"open_orders row is missing {k}"
    return f"{r['total']} orders, {sum(1 for x in r['rows'] if x['order_type'] == 'Import')} import"


def _po_page():
    p = call("po.po_page", name=PO)
    assert p["lines"] and p["taxes"], "no lines or taxes"
    leaked = PRICE_FIELDS & set(p["lines"][0].keys())
    assert not leaked, f"price fields leaked to the client: {leaked}"
    assert "kes" in p["taxes"][0]
    return f"{p['name']} {p['workflow_state']}, {len(p['lines'])} lines, actions {[a['action'] for a in p['actions']]}"


def _tax_rows():
    r = call("po.tax_template_rows", template="Importation - VCL")
    types = {x["description"]: x["charge_type"] for x in r}
    assert types.get("VAT") == "On Previous Row Total", f"VAT is {types.get('VAT')}: the template fix is not in place"
    return f"{len(r)} rows, VAT = {types['VAT']}"


def _pi_page():
    p = call("pi.pi_page", name=PI)
    assert p["is_import"] and "dates" in p and "requirements" in p
    assert p["shipment"]["containers"] is not None
    return f"{p['name']} docstatus {p['docstatus']}, ready to submit: {p['can']['submit']}, missing {p['requirements']['missing']}"


def _raise_options():
    o = call("po.raise_options")
    assert o["tax_defaults"]["Import"] == "Importation - VCL" and "USD" in o["currencies"] and o["payment_terms"]
    return f"{len(o['tax_templates'])} tax templates, {len(o['payment_terms'])} terms"


def _schedule_preview():
    r = call("pi.schedule_preview", name=PI, payload={"terms_template": "180 Days", "tax_days_after_landing": 2,
                                                    "expected_delivery": "2026-10-01"})
    assert not r["missing"] and r["rows"][1]["due_date"] == "2026-10-03", r
    return f"net due {r['rows'][0]['due_date']}, taxes due {r['rows'][1]['due_date']}"


def _attachments():
    p = call("pi.pi_page", name=PI)
    assert p["attachments"] is not None, "attachments could not be listed"
    return f"{len(p['attachments'])} files"


def _pay_context():
    c = call("pi.pay_context", pi=PI)
    assert c["accounts"] and c["modes"]
    return f"{len(c['accounts'])} accounts, {len(c['modes'])} modes, {len(c['rows'])} rows to pay"


print(f"Smoke check against {BASE}")
check("search_items: any words, any order", _search_items)
check("uom_factor / uom_list", _uom)
check("open_orders: new fields", _open_orders)
check("draft_orders", lambda: f"{len(call('purchasing.draft_orders'))} drafts")
check(f"po_page {PO}: no price fields leak", _po_page)
check("tax_template_rows: Importation - VCL is fixed", _tax_rows)
check(f"pi_page {PI}", _pi_page)
check("raise_options", _raise_options)
check("schedule_preview (server-side rule, nothing saved)", _schedule_preview)
check("attachments listed server-side", _attachments)
check("pay_context (read only)", _pay_context)
print("\nAll good." if not fails else f"\n{len(fails)} failed: {fails}")
sys.exit(1 if fails else 0)
