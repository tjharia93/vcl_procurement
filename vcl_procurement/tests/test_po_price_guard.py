"""The Labchem case, reproduced.

Runs without a bench: `frappe` and `frappe.utils.flt` are stubbed, because the
logic under test is arithmetic on a document, not anything Frappe-specific.

    python3 vcl_procurement/tests/test_po_price_guard.py
"""
import sys
import types
import unittest

# --- stub frappe before importing the module under test ----------------------
frappe = types.ModuleType("frappe")
frappe.logged = []
frappe.log_error = lambda title, message: frappe.logged.append((title, message))
utils = types.ModuleType("frappe.utils")
utils.flt = lambda v: float(v or 0)
frappe.utils = utils
sys.modules.setdefault("frappe", frappe)
sys.modules.setdefault("frappe.utils", utils)

sys.path.insert(0, __file__.rsplit("/vcl_procurement/tests/", 1)[0])
from vcl_procurement.api.po_price_guard import normalise_line_prices  # noqa: E402


class Item(dict):
    """Stands in for a Purchase Order Item — attribute access over a dict."""

    def __getattr__(self, k):
        return self.get(k)

    def __setattr__(self, k, v):
        self[k] = v

    def set(self, k, v):
        self[k] = v


class Doc:
    def __init__(self, items, name="PUR-ORD-TEST"):
        self.items = items
        self.name = name
        self.comments = []

    def get(self, k):
        return getattr(self, k, None)

    def is_new(self):
        return False

    def add_comment(self, _type, text):
        self.comments.append(text)


class PoPriceGuard(unittest.TestCase):

    def test_margin_is_cleared_and_rate_is_kept(self):
        """The exact Labchem line: 250 stored as 220 + a margin of 30."""
        item = Item(idx=1, item_code="I.P. ALCOHOL", qty=100, rate=250,
                    price_list_rate=220, margin_type="Amount",
                    margin_rate_or_amount=30, rate_with_margin=250,
                    discount_percentage=0, discount_amount=0)
        doc = Doc([item])
        normalise_line_prices(doc)

        self.assertEqual(item.rate, 250, "the agreed price must not move")
        self.assertEqual(item.price_list_rate, 250, "base must agree with rate")
        self.assertEqual(item.margin_rate_or_amount, 0)
        self.assertEqual(item.margin_type, "")
        self.assertEqual(item.rate_with_margin, 0)

    def test_a_second_edit_can_no_longer_inflate_the_price(self):
        """This is the bug: edit a cleaned line and the price stays put."""
        item = Item(idx=1, item_code="I.P. ALCOHOL", qty=100, rate=250,
                    price_list_rate=220, margin_type="Amount",
                    margin_rate_or_amount=30, rate_with_margin=250,
                    discount_percentage=0, discount_amount=0)
        doc = Doc([item])
        normalise_line_prices(doc)

        # somebody opens it on a phone and saves again, as happened
        normalise_line_prices(doc)

        self.assertEqual(item.rate, 250, "280 is what used to happen here")

    def test_discounts_are_cleared_too(self):
        item = Item(idx=1, item_code="Clear Glue", qty=500, rate=65,
                    price_list_rate=70, margin_type="",
                    margin_rate_or_amount=0,
                    discount_percentage=7.14, discount_amount=5)
        doc = Doc([item])
        normalise_line_prices(doc)

        self.assertEqual(item.rate, 65)
        self.assertEqual(item.price_list_rate, 65)
        self.assertEqual(item.discount_percentage, 0)
        self.assertEqual(item.discount_amount, 0)

    def test_a_clean_line_is_left_completely_alone(self):
        item = Item(idx=1, item_code="Clear Glue", qty=500, rate=65,
                    price_list_rate=65, margin_type="",
                    margin_rate_or_amount=0,
                    discount_percentage=0, discount_amount=0)
        doc = Doc([item])
        normalise_line_prices(doc)

        self.assertEqual(doc.comments, [], "no note for a line nothing happened to")

    def test_zero_rate_lines_are_skipped(self):
        """Blank placeholder rows must not be touched."""
        item = Item(idx=1, item_code=None, qty=0, rate=0, price_list_rate=0,
                    margin_rate_or_amount=0, discount_percentage=0,
                    discount_amount=0)
        doc = Doc([item])
        normalise_line_prices(doc)
        self.assertEqual(item.rate, 0)

    def test_it_says_what_it_did(self):
        item = Item(idx=1, item_code="I.P. ALCOHOL", qty=100, rate=250,
                    price_list_rate=220, margin_type="Amount",
                    margin_rate_or_amount=30, rate_with_margin=250,
                    discount_percentage=0, discount_amount=0)
        doc = Doc([item])
        normalise_line_prices(doc)

        self.assertTrue(doc.comments, "a silent correction is its own trap")
        note = doc.comments[0]
        self.assertIn("I.P. ALCOHOL", note)
        self.assertIn("margin Amount 30", note)
        self.assertIn("250", note)


if __name__ == "__main__":
    unittest.main(verbosity=2)
