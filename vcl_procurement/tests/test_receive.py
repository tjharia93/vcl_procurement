"""The receive-goods rules, tested without a bench. Numbers are PUR-ORD-2026-00203 / ACC-PINV-2026-00586.

    python3 vcl_procurement/tests/test_receive.py
"""
import sys
import unittest

sys.path.insert(0, __file__.rsplit("/vcl_procurement/tests/", 1)[0])
from vcl_procurement.api import rules  # noqa: E402


class Allowance(unittest.TestCase):
    def test_item_allowance_wins_when_set(self):
        self.assertEqual(rules.allowance_pct(5, 1), 5.0)

    def test_falls_back_to_stock_settings(self):
        self.assertEqual(rules.allowance_pct(0, 1), 1.0)
        self.assertEqual(rules.allowance_pct(None, 1), 1.0)

    def test_none_anywhere_is_zero(self):
        self.assertEqual(rules.allowance_pct(0, 0), 0.0)

    def test_max_qty_is_pending_plus_allowance(self):
        self.assertEqual(rules.max_receipt_qty(4, 1), 4.04)
        self.assertEqual(rules.max_receipt_qty(3, 1), 3.03)
        self.assertEqual(rules.max_receipt_qty(15, 1), 15.15)

    def test_max_qty_is_never_rounded_up(self):
        self.assertLessEqual(rules.max_receipt_qty(3.333, 1), 3.333 * 1.01)

    def test_nothing_pending_means_nothing_more(self):
        self.assertEqual(rules.max_receipt_qty(0, 5), 0.0)
        self.assertEqual(rules.max_receipt_qty(-2, 5), 0.0)


class Prefill(unittest.TestCase):
    def test_no_invoice_starts_at_pending(self):
        self.assertEqual(rules.start_receipt_qty(4, 4.04), 4.0)

    def test_invoice_qty_used_as_is_when_within_the_max(self):
        self.assertEqual(rules.start_receipt_qty(3, 3.03, 2.995), 2.995)

    def test_invoice_over_the_max_is_held_to_it(self):
        # ACC-PINV-2026-00586 bills 4.185 against a PO of 4: the box fills with 4.04
        self.assertEqual(rules.start_receipt_qty(4, 4.04, 4.185), 4.04)

    def test_line_the_invoice_does_not_bill_starts_at_zero(self):
        self.assertEqual(rules.start_receipt_qty(4, 4.04, 0), 0.0)

    def test_invoice_rows_match_po_rows_on_po_detail(self):
        po = [{"name": "a1", "idx": 1}, {"name": "a2", "idx": 2}]
        inv = [{"po_detail": "a2", "qty": 4.185}, {"po_detail": "a1", "qty": 2.0}, {"po_detail": "a1", "qty": 0.995}, {"po_detail": "zz", "qty": 9}]
        self.assertEqual(rules.invoice_qty_by_line(po, inv), {2: 4.185, 1: 2.995})


class Lines(unittest.TestCase):
    def test_within_the_max_passes(self):
        rules.check_receipt_lines({1: 3.03, 2: 4}, {1: 3.03, 2: 4.04})

    def test_above_the_max_is_refused_in_words(self):
        with self.assertRaises(rules.RuleError) as cm:
            rules.check_receipt_lines({2: 4.185}, {2: 4.04})
        self.assertIn("at most 4.04", str(cm.exception))

    def test_nothing_to_receive_is_refused(self):
        with self.assertRaises(rules.RuleError):
            rules.check_receipt_lines({1: 0}, {1: 3})

    def test_unknown_line_is_refused(self):
        with self.assertRaises(rules.RuleError):
            rules.check_receipt_lines({9: 1}, {1: 3})


class Unbilled(unittest.TestCase):
    def test_left_to_bill_per_line(self):
        rows = [{"idx": 1, "amount": 1680, "billed_amt": 1677.2}, {"idx": 2, "amount": 2240, "billed_amt": 2343.6},
                {"idx": 3, "amount": 8400, "billed_amt": 8200.08}]
        self.assertEqual(rules.unbilled_lines(rows), [{"idx": 1, "left": 2.8}, {"idx": 3, "left": 199.92}])

    def test_fully_billed_has_none(self):
        self.assertEqual(rules.unbilled_lines([{"idx": 1, "amount": 10, "billed_amt": 10}]), [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
