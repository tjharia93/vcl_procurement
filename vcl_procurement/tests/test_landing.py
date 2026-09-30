"""The pure parts of the procurement landing APIs, checked without a bench.

    python3 vcl_procurement/tests/test_landing.py
"""
import importlib.util
import pathlib
import unittest

_p = pathlib.Path(__file__).resolve().parents[1] / "api" / "landing_rules.py"
_s = importlib.util.spec_from_file_location("landing_rules", _p)
R = importlib.util.module_from_spec(_s)
_s.loader.exec_module(R)


class TestContainers(unittest.TestCase):
    def test_normalise(self):
        self.assertEqual(R.norm_container("msku 9594-207 "), "MSKU9594207")
        self.assertEqual(R.norm_container(None), "")

    def test_partial_and_spaced_query_matches(self):
        self.assertTrue(R.container_matches("MSKU9594207", "msku 9594"))
        self.assertTrue(R.container_matches("MRKU3476972 ", "3476"))

    def test_short_query_matches_nothing(self):
        self.assertFalse(R.container_matches("MSKU9594207", "ms"))
        self.assertFalse(R.container_matches("MSKU9594207", "  "))

    def test_non_match(self):
        self.assertFalse(R.container_matches("MSKU9594207", "TRHU"))


class TestGrouping(unittest.TestCase):
    def test_pos_grouped_sorted_unique(self):
        rows = [{"parent": "A", "purchase_order": "PO-2"}, {"parent": "A", "purchase_order": "PO-1"},
                {"parent": "A", "purchase_order": "PO-2"}, {"parent": "B", "purchase_order": None}, {"parent": "C", "purchase_order": "PO-9"}]
        self.assertEqual(R.group_pos(rows), {"A": ["PO-1", "PO-2"], "C": ["PO-9"]})

    def test_items_keep_order(self):
        rows = [{"parent": "M", "item_name": "x", "qty": 1, "uom": "Kg"}, {"parent": "M", "item_name": "y", "qty": 2, "uom": "Nos"}]
        self.assertEqual([i["item_name"] for i in R.group_items(rows)["M"]], ["x", "y"])

    def test_open_mr(self):
        self.assertTrue(R.is_open_mr({"per_ordered": 0, "status": "Pending"}))
        self.assertFalse(R.is_open_mr({"per_ordered": 100, "status": "Ordered"}))
        self.assertFalse(R.is_open_mr({"per_ordered": 40, "status": "Stopped"}))


if __name__ == "__main__":
    unittest.main(verbosity=1)
