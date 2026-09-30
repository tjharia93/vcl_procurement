"""The purchasing rules, tested without a bench. Numbers are the real ones from
the 29-30 Sep 2026 session (ACC-PINV-2026-00594, PUR-ORD-2026-00237).

    python3 vcl_procurement/tests/test_rules.py
"""
import sys
import unittest

sys.path.insert(0, __file__.rsplit("/vcl_procurement/tests/", 1)[0])
from vcl_procurement.api import rules  # noqa: E402

DAYS_180 = {"due_date_based_on": "Day(s) after invoice date", "credit_days": 180, "credit_months": 0}
EOM_60 = {"due_date_based_on": "Day(s) after the end of the invoice month", "credit_days": 60, "credit_months": 0}


class TestDates(unittest.TestCase):
    def test_180_days_from_bill_date(self):
        # 00594: bill 11-08-2026 + 180 days
        self.assertEqual(rules.term_due_date("2026-08-11", DAYS_180), "2027-02-07")

    def test_matches_submitted_import_invoices(self):
        # Checked against live: due = bill date + credit days, whatever the posting date.
        self.assertEqual(rules.term_due_date("2026-08-25", DAYS_180), "2027-02-21")   # ACC-PINV-2026-00586
        self.assertEqual(rules.term_due_date("2026-01-28", DAYS_180), "2026-07-27")   # ACC-PINV-2026-00227
        self.assertEqual(rules.term_due_date("2026-03-07", {"credit_days": 120}), "2026-07-05")  # ...00502

    def test_end_of_month_terms(self):
        self.assertEqual(rules.term_due_date("2026-08-11", EOM_60), "2026-10-30")

    def test_months_after_end_of_month(self):
        t = {"due_date_based_on": "Month(s) after the end of the invoice month", "credit_months": 2}
        self.assertEqual(rules.term_due_date("2026-08-11", t), "2026-10-31")
        self.assertEqual(rules.term_due_date("2026-11-30", {**t, "credit_months": 3}), "2027-02-28")

    def test_no_bill_date_or_term(self):
        self.assertIsNone(rules.term_due_date(None, DAYS_180))
        self.assertIsNone(rules.term_due_date("2026-08-11", None))

    def test_taxes_due_after_landing(self):
        self.assertEqual(rules.tax_due_date("2026-10-01"), "2026-10-03")
        self.assertEqual(rules.tax_due_date("2026-10-01", 5), "2026-10-06")
        self.assertIsNone(rules.tax_due_date(None))


class TestCurrency(unittest.TestCase):
    def test_kes_to_usd_at_the_rate(self):
        self.assertAlmostEqual(rules.kes_to_doc(1160085.97, 129.61, "USD"), 8950.59, 2)

    def test_kes_document_is_unchanged(self):
        self.assertEqual(rules.kes_to_doc(1000, 1, "KES"), 1000.0)

    def test_round_trip(self):
        self.assertAlmostEqual(rules.doc_to_kes(rules.kes_to_doc(895488.45, 129.61, "USD"), 129.61, "USD"), 895488.45, 6)

    def test_zero_rate_refused(self):
        with self.assertRaises(rules.RuleError):
            rules.kes_to_doc(100, 0, "USD")


class TestImportInvoiceGate(unittest.TestCase):
    def test_posting_date_follows_kra_entry_date_for_imports(self):
        self.assertEqual(rules.posting_date_for("Importation", "2026-09-27", "2026-09-29"), "2026-09-27")

    def test_other_types_do_not_move(self):
        self.assertEqual(rules.posting_date_for("Local Purchase", "2026-09-27", "2026-09-29"), "2026-09-29")
        self.assertEqual(rules.posting_date_for("Importation costs", "2026-09-27", "2026-09-29"), "2026-09-29")

    def test_no_entry_date_leaves_posting_alone(self):
        self.assertEqual(rules.posting_date_for("Importation", None, "2026-09-29"), "2026-09-29")

    def test_gate_lists_everything_missing(self):
        self.assertEqual(rules.kra_gate_missing("Importation", "2026-08-11", None, ""),
                         ["KRA customs entry date", "KRA customs entry number"])
        self.assertEqual(len(rules.kra_gate_missing("Importation", None, None, None)), 3)

    def test_gate_open_when_complete(self):
        self.assertEqual(rules.kra_gate_missing("Importation", "2026-08-11", "2026-09-27", "26MBA|M406322591"), [])

    def test_gate_does_not_apply_to_other_types(self):
        self.assertEqual(rules.kra_gate_missing("Local Purchase", None, None, None), [])
        self.assertEqual(rules.kra_gate_missing("Importation costs", None, None, None), [])
        self.assertEqual(rules.kra_gate_missing(None, None, None, None), [])

    def test_blank_entry_number_counts_as_missing(self):
        self.assertEqual(rules.kra_gate_missing("Importation", "2026-08-11", "2026-09-27", "   "),
                         ["KRA customs entry number"])


class TestImportSchedule(unittest.TestCase):
    def test_two_rows_for_00594(self):
        # net USD 41,529.05 (rounded), taxes USD 8,949.92, eta 16-09-2026, 180 days from 11-08-2026
        rows, missing = rules.import_schedule(41529.0486, 8949.9214, "2026-08-11", DAYS_180, "180 Days", "2026-09-16")
        self.assertEqual(missing, [])
        self.assertEqual(rows[0]["due_date"], "2027-02-07")
        self.assertEqual(rows[1]["due_date"], "2026-09-18")
        self.assertEqual(rows[0]["payment_term"], "180 Days")
        self.assertIsNone(rows[1]["payment_term"])
        self.assertAlmostEqual(rows[0]["invoice_portion"] + rows[1]["invoice_portion"], 100.0, 6)
        # ERPNext recomputes each amount from the portion, so it must not be rounded to 2 dp:
        # 41,528.379 / 50,478.97 = 82.2683...%, not 82.27%.
        self.assertNotEqual(rows[0]["invoice_portion"], round(rows[0]["invoice_portion"], 2))
        grand = rows[0]["payment_amount"] + rows[1]["payment_amount"]
        self.assertAlmostEqual(rows[0]["invoice_portion"] / 100 * grand, rows[0]["payment_amount"], 4)
        self.assertAlmostEqual(rows[0]["payment_amount"] + rows[1]["payment_amount"], 50478.97, 6)

    def test_missing_pieces_are_named_and_nothing_is_returned(self):
        rows, missing = rules.import_schedule(100, 20, None, DAYS_180, "180 Days", None)
        self.assertIsNone(rows)
        self.assertEqual(missing, ["supplier invoice date (bill date)", "expected delivery to port"])
        rows, missing = rules.import_schedule(100, 20, "2026-08-11", None, None, "2026-09-16")
        self.assertIsNone(rows)
        self.assertEqual(missing, ["payment terms"])

    def test_custom_days_after_landing(self):
        rows, _ = rules.import_schedule(100, 20, "2026-08-11", DAYS_180, "180 Days", "2026-10-01", 5)
        self.assertEqual(rows[1]["due_date"], "2026-10-06")
        self.assertIn("5 days", rows[1]["description"])

    def test_zero_grand_total_does_not_divide(self):
        rows, _ = rules.import_schedule(0, 0, "2026-08-11", DAYS_180, "180 Days", "2026-10-01")
        self.assertEqual(rows[0]["invoice_portion"], 100.0)


class TestUnits(unittest.TestCase):
    def test_stock_unit_is_one(self):
        self.assertEqual(rules.uom_factor("Kg", "Kg", []), 1.0)

    def test_items_own_row_first(self):
        self.assertEqual(rules.uom_factor("Roll", "Kg", [{"uom": "Roll", "conversion_factor": 12.5}], 1000), 12.5)

    def test_site_table_either_way(self):
        self.assertEqual(rules.uom_factor("Tonne", "Kg", [], global_factor_to_stock=1000), 1000.0)
        self.assertEqual(rules.uom_factor("Tonne", "Kg", [], global_factor_from_stock=0.001), 1000.0)

    def test_unknown_is_none(self):
        self.assertIsNone(rules.uom_factor("Roll", "Kg", []))

    def test_line_without_conversion_is_refused(self):
        with self.assertRaises(rules.RuleError) as cm:
            rules.check_line_units([{"item_code": "NCR-X", "uom": "Tonne"}], {"NCR-X": "Kg"})
        self.assertIn("1 Tonne", str(cm.exception))

    def test_line_with_conversion_passes(self):
        rules.check_line_units([{"item_code": "NCR-X", "uom": "Tonne", "conversion_factor": 1000}], {"NCR-X": "Kg"})
        rules.check_line_units([{"item_code": "NCR-X", "uom": "Kg"}], {"NCR-X": "Kg"})


class TestSearch(unittest.TestCase):
    def test_words_in_any_order(self):
        # the search that returned nothing before
        toks = rules.tokens_of("ncr%cfb%whi")
        self.assertTrue(rules.matches("NCR-Reel-250-53-WHITE-CFB", toks))
        self.assertTrue(rules.matches("NCR-250-55-WHI-CB whi ncr", rules.tokens_of("whi ncr cb")))

    def test_every_word_must_match(self):
        self.assertFalse(rules.matches("NCR-Reel-250-53-WHITE", rules.tokens_of("ncr cfb")))

    def test_empty_query_has_no_tokens(self):
        self.assertEqual(rules.tokens_of("  %  "), [])


class TestTaxRows(unittest.TestCase):
    def rows(self):
        return [
            {"charge_type": "Actual", "add_deduct_tax": "Add", "base_tax_amount": 0},
            {"charge_type": "On Previous Row Total", "row_id": 1, "add_deduct_tax": "Add", "base_tax_amount": 895488.45},
            {"charge_type": "On Previous Row Amount", "row_id": 1, "add_deduct_tax": "Deduct", "base_tax_amount": 100},
        ]

    def test_good_rows_pass(self):
        rules.check_tax_rows(self.rows())

    def test_previous_row_needs_a_row_number(self):
        r = self.rows(); r[1]["row_id"] = None
        with self.assertRaises(rules.RuleError):
            rules.check_tax_rows(r)

    def test_cannot_point_at_itself_or_later_rows(self):
        r = self.rows(); r[1]["row_id"] = 2
        with self.assertRaises(rules.RuleError):
            rules.check_tax_rows(r)

    def test_unknown_type_refused(self):
        with self.assertRaises(rules.RuleError):
            rules.check_tax_rows([{"charge_type": "Percent"}])

    def test_signed_sum(self):
        self.assertAlmostEqual(rules.signed_sum(self.rows(), "base_tax_amount"), 895388.45)


if __name__ == "__main__":
    unittest.main(verbosity=1)
