"""Raising a purchase order, tested where it can be tested without a bench.

`create_purchase_order` builds a Frappe document, so the parts worth asserting
without a site are the refusals and the line shaping — the places a bad call
should stop rather than quietly create something wrong.

    python3 vcl_procurement/tests/test_raise_po.py
"""
import datetime
import sys
import types
import unittest

frappe = types.ModuleType("frappe")


class _Thrown(Exception):
    pass


def _throw(msg, exc=None):
    raise _Thrown(msg)


frappe.whitelist = lambda *a, **k: (lambda f: f)
frappe.throw = _throw
frappe._ = lambda s: s
frappe.PermissionError = PermissionError
frappe.get_roles = lambda: ["Purchase Manager"]

utils = types.ModuleType("frappe.utils")
utils.flt = lambda v: float(v or 0)


def _getdate(v):
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    return datetime.date.fromisoformat(str(v)[:10])


utils.getdate = _getdate
utils.nowdate = lambda: datetime.date.today().isoformat()
utils.add_days = lambda d, n: (_getdate(d) + datetime.timedelta(days=n)).isoformat()
utils.today = utils.nowdate
utils.date_diff = lambda a, b: (_getdate(a) - _getdate(b)).days
frappe.utils = utils
sys.modules.setdefault("frappe", frappe)
sys.modules.setdefault("frappe.utils", utils)

sys.path.insert(0, __file__.rsplit("/vcl_procurement/tests/", 1)[0])
from vcl_procurement.api import raise_po  # noqa: E402


class Row(dict):
    def __getattr__(self, k):
        return self.get(k)

    def __setattr__(self, k, v):
        self[k] = v


class Doc:
    """Just enough Purchase Order to see what the API asks for."""

    def __init__(self):
        self.items = []
        self.name = "PUR-ORD-TEST"
        self.docstatus = 0
        self.status = "Draft"
        self.currency = "KES"
        self.grand_total = 0
        self.inserted = False

    def append(self, _field, _values):
        row = Row()
        self.items.append(row)
        return row

    def insert(self):
        self.inserted = True

    def save(self):
        self.saved = True


class RaisingAPurchaseOrder(unittest.TestCase):

    def setUp(self):
        self.doc = Doc()
        frappe.new_doc = lambda _dt: self.doc
        self.tables = {}
        frappe.get_all = lambda dt, filters=None, fields=None, pluck=None, **k: (
            [r[pluck] for r in self.tables.get(dt, [])] if pluck else [Row(r) for r in self.tables.get(dt, [])])

    def call(self, **kw):
        kw.setdefault("supplier", "LABCHEM LTD")
        kw.setdefault("lines", [{"item_code": "I.P. ALCOHOL", "qty": 100, "rate": 250}])
        return raise_po.create_purchase_order(**kw)

    def test_it_creates_a_draft_and_says_so(self):
        out = self.call()
        self.assertTrue(self.doc.inserted)
        self.assertEqual(out["docstatus"], 0)
        self.assertEqual(out["lines"], 1)

    def test_rate_is_the_only_price_field_it_sets(self):
        """If it never sets a margin, a phone can never send one."""
        self.call()
        line = self.doc.items[0]
        self.assertEqual(line.rate, 250)
        for banned in ("margin_type", "margin_rate_or_amount", "price_list_rate",
                       "discount_percentage", "discount_amount", "rate_with_margin"):
            self.assertNotIn(banned, line, f"{banned} must not be set from a client")

    def test_a_line_with_no_quantity_is_refused(self):
        with self.assertRaises(_Thrown):
            self.call(lines=[{"item_code": "I.P. ALCOHOL", "qty": 0, "rate": 250}])
        self.assertFalse(self.doc.inserted, "nothing should be created")

    def test_a_line_with_no_item_is_refused(self):
        with self.assertRaises(_Thrown):
            self.call(lines=[{"qty": 5, "rate": 250}])

    def test_no_supplier_is_refused(self):
        with self.assertRaises(_Thrown):
            self.call(supplier="")

    def test_no_lines_is_refused(self):
        with self.assertRaises(_Thrown):
            self.call(lines=[])

    def test_lines_may_arrive_as_a_json_string(self):
        """Retrofit and the browser both send it that way."""
        out = self.call(lines='[{"item_code":"Clear Glue","qty":500,"rate":65}]')
        self.assertEqual(out["lines"], 1)
        self.assertEqual(self.doc.items[0].rate, 65)

    def test_a_zero_rate_is_allowed(self):
        """A material request has no price. Refusing it would block the very
        flow that should be commonest — raise the PO, then fill in the quote."""
        self.call(lines=[{"item_code": "I.P. ALCOHOL", "qty": 10, "rate": 0}])
        self.assertEqual(self.doc.items[0].rate, 0)

    def test_the_material_request_link_is_carried_through(self):
        self.call(lines=[{
            "item_code": "I.P. ALCOHOL", "qty": 100, "rate": 250,
            "material_request": "MAT-MR-2026-00071",
            "material_request_item": "fqc461c0mk",
        }])
        line = self.doc.items[0]
        self.assertEqual(line.material_request, "MAT-MR-2026-00071")
        self.assertEqual(line.material_request_item, "fqc461c0mk")

    def test_the_default_delivery_date_is_a_week_out(self):
        self.call()
        wanted = _getdate(utils.add_days(utils.nowdate(), 7))
        self.assertEqual(self.doc.schedule_date, wanted)

    def test_an_explicit_delivery_date_wins(self):
        self.call(schedule_date="2026-09-30")
        self.assertEqual(self.doc.schedule_date, datetime.date(2026, 9, 30))

    def test_a_chosen_order_date_is_stored_as_text(self):
        # ERPNext's payment-terms lookup takes the order date typed `str | None`;
        # a date object made every PO with payment terms fail (FrappeTypeError).
        self.call(order_date="2026-10-16", schedule_date="2026-10-23")
        self.assertEqual(self.doc.transaction_date, "2026-10-16")
        self.assertIsInstance(self.doc.transaction_date, str)


    def test_a_line_that_lost_its_request_is_linked_back(self):
        """PUR-ORD-2026-00270: Clear Glue copied from the last order, MR-00079 left at 0%."""
        self.tables = {
            "Material Request Item": [{"name": "fnohibsijm", "parent": "MAT-MR-2026-00079",
                                       "item_code": "Clear Glue", "stock_qty": 500, "ordered_qty": 0}],
            "Material Request": [{"name": "MAT-MR-2026-00079"}],
        }
        self.call(lines=[{"item_code": "Clear Glue", "qty": 500, "rate": 65}])
        line = self.doc.items[0]
        self.assertEqual(line.material_request, "MAT-MR-2026-00079")
        self.assertEqual(line.material_request_item, "fnohibsijm")
        self.assertTrue(getattr(self.doc, "saved", False))

    def test_no_open_request_means_no_link_and_no_second_save(self):
        self.call()
        self.assertIsNone(self.doc.items[0].material_request)
        self.assertFalse(getattr(self.doc, "saved", False))


class MatchingLinesToRequests(unittest.TestCase):
    from vcl_procurement.api import rules as R
    match = staticmethod(R.match_material_requests)

    def row(self, mri, item="Clear Glue", pending=500, mr="MR1"):
        return {"material_request": mr, "material_request_item": mri, "item_code": item, "pending": pending}

    def test_one_open_request_with_room_is_linked(self):
        out = self.match([{"item_code": "Clear Glue", "stock_qty": 500}], [self.row("a")])
        self.assertEqual([(i, r["material_request_item"]) for i, r in out], [(0, "a")])

    def test_two_open_requests_for_the_item_is_left_to_the_buyer(self):
        out = self.match([{"item_code": "Clear Glue", "stock_qty": 100}],
                         [self.row("a"), self.row("b", mr="MR2")])
        self.assertEqual(out, [])

    def test_ordering_more_than_the_request_left_does_not_link(self):
        self.assertEqual(self.match([{"item_code": "Clear Glue", "stock_qty": 600}], [self.row("a")]), [])

    def test_a_line_already_linked_is_left_alone_and_uses_up_its_row(self):
        lines = [{"item_code": "Clear Glue", "stock_qty": 400, "material_request": "MR1", "material_request_item": "a"},
                 {"item_code": "Clear Glue", "stock_qty": 200}]
        self.assertEqual(self.match(lines, [self.row("a")]), [])

    def test_a_different_item_never_links(self):
        self.assertEqual(self.match([{"item_code": "Hot Melt", "stock_qty": 5}], [self.row("a")]), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
