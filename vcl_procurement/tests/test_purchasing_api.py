"""The shaping is what both clients depend on, so the shaping is what is tested.

Runs without a bench — `frappe` is stubbed, because these are pure functions
over a dict.

    python3 vcl_procurement/tests/test_purchasing_api.py
"""
import datetime
import sys
import types
import unittest

frappe = types.ModuleType("frappe")
frappe.whitelist = lambda *a, **k: (lambda f: f)
frappe.throw = lambda msg: (_ for _ in ()).throw(Exception(msg))
frappe._ = lambda s: s
utils = types.ModuleType("frappe.utils")
utils.flt = lambda v: float(v or 0)


def _getdate(v):
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.date() if isinstance(v, datetime.datetime) else v
    return datetime.date.fromisoformat(str(v)[:10])


utils.getdate = _getdate
frappe.utils = utils
sys.modules.setdefault("frappe", frappe)
sys.modules.setdefault("frappe.utils", utils)

sys.path.insert(0, __file__.rsplit("/vcl_procurement/tests/", 1)[0])
from vcl_procurement.api.purchasing import _shape_line, _shape_order  # noqa: E402

# The Labchem line as ERPNext actually held it — margin and all.
LABCHEM_LINE = {
    "idx": 1, "item_code": "I.P. ALCOHOL", "item_name": "I.P. ALCOHOL",
    "qty": 100, "uom": "Litre", "rate": 250, "amount": 25000,
    "price_list_rate": 220, "margin_type": "Amount", "margin_rate_or_amount": 30,
    "rate_with_margin": 250, "discount_percentage": 0, "discount_amount": 0,
    "schedule_date": "2026-09-07", "warehouse": "Stores - VCL",
    "material_request": "MAT-MR-2026-00071", "received_qty": 0,
}

ORDER = {
    "name": "PUR-ORD-2026-00221-2", "supplier": "LABCHEM LTD",
    "supplier_name": "LABCHEM LTD", "transaction_date": "2026-09-04",
    "schedule_date": "2026-09-07", "status": "Draft", "workflow_state": "Draft",
    "currency": "KES", "grand_total": 66700, "per_received": 0, "per_billed": 0,
    "docstatus": 0,
}

WITHHELD = ("price_list_rate", "margin_type", "margin_rate_or_amount",
            "rate_with_margin", "discount_percentage", "discount_amount")


class Shaping(unittest.TestCase):

    def test_no_price_field_but_rate_ever_reaches_a_client(self):
        """The whole point. A screen cannot show what it is never sent."""
        line = _shape_line(LABCHEM_LINE)
        for field in WITHHELD:
            self.assertNotIn(field, line, f"{field} must never leave the server")
        self.assertEqual(line["rate"], 250)
        self.assertEqual(line["amount"], 25000)

    def test_the_line_keeps_what_a_buyer_actually_needs(self):
        line = _shape_line(LABCHEM_LINE)
        self.assertEqual(line["item_name"], "I.P. ALCOHOL")
        self.assertEqual(line["qty"], 100)
        self.assertEqual(line["uom"], "Litre")
        self.assertEqual(line["material_request"], "MAT-MR-2026-00071")

    def test_item_name_falls_back_to_the_code(self):
        line = _shape_line(dict(LABCHEM_LINE, item_name=None))
        self.assertEqual(line["item_name"], "I.P. ALCOHOL")

    def test_dates_are_iso_so_both_clients_parse_them_the_same(self):
        line = _shape_line(LABCHEM_LINE)
        order = _shape_order(ORDER)
        self.assertEqual(line["due"], "2026-09-07")
        self.assertEqual(order["date"], "2026-09-04")
        self.assertIsNone(_shape_order(dict(ORDER, schedule_date=None))["due"])

    def test_a_datetime_shapes_the_same_as_a_string(self):
        as_dt = _shape_line(dict(LABCHEM_LINE,
                                 schedule_date=datetime.date(2026, 9, 7)))
        self.assertEqual(as_dt["due"], "2026-09-07")

    def test_draft_and_cancelled_are_explicit_not_inferred_from_a_number(self):
        self.assertTrue(_shape_order(ORDER)["is_draft"])
        self.assertFalse(_shape_order(ORDER)["is_cancelled"])
        cancelled = _shape_order(dict(ORDER, docstatus=2))
        self.assertTrue(cancelled["is_cancelled"])
        self.assertFalse(cancelled["is_draft"])

    def test_received_and_billed_are_booleans_not_percentages(self):
        """A phone should not be doing >= 100 arithmetic to draw a tick."""
        done = _shape_order(dict(ORDER, per_received=100, per_billed=100))
        self.assertTrue(done["fully_received"])
        self.assertTrue(done["fully_billed"])
        part = _shape_order(dict(ORDER, per_received=40))
        self.assertFalse(part["fully_received"])

    def test_supplier_name_falls_back_to_the_id(self):
        self.assertEqual(
            _shape_order(dict(ORDER, supplier_name=None))["supplier_name"],
            "LABCHEM LTD")

    def test_totals_are_floats_not_strings(self):
        order = _shape_order(dict(ORDER, grand_total="66700"))
        self.assertIsInstance(order["total"], float)
        self.assertEqual(order["total"], 66700.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
