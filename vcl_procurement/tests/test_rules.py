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

    def test_taxes_due_is_the_earlier_of_landing_and_thirty_days_from_invoice(self):
        # invoice 11/08 -> cap 10/09; lands 16/09 -> 18/09: the cap is earlier
        self.assertEqual(rules.tax_due_date("2026-09-16", 2, "2026-08-11"), "2026-09-10")
        # invoice 20/09 -> cap 20/10; lands 01/10 -> 03/10: landing is earlier
        self.assertEqual(rules.tax_due_date("2026-10-01", 2, "2026-09-20"), "2026-10-03")
        # no landing date: unknown, the cap alone is not a due date
        self.assertIsNone(rules.tax_due_date(None, 2, "2026-08-11"))
        # no invoice date: landing rule only
        self.assertEqual(rules.tax_due_date("2026-10-01", 2, None), "2026-10-03")


class TestRebaseDueDates(unittest.TestCase):
    """Bill from a PO: the mapped schedule counts from today, the terms count from the invoice date."""

    def test_rows_recounted_from_bill_date_and_invoice_due_is_the_latest(self):
        # Bill date 23/09/2026 + 180 days = 22/03/2027 (the date ERPNext quoted).
        dues, last = rules.rebase_due_dates([DAYS_180], "2026-09-23")
        self.assertEqual(dues, ["2027-03-22"])
        self.assertEqual(last, "2027-03-22")

    def test_two_rows_take_the_latest(self):
        dues, last = rules.rebase_due_dates([DAYS_180, {"due_date_based_on": "Day(s) after invoice date", "credit_days": 0}], "2026-09-23")
        self.assertEqual(dues, ["2027-03-22", "2026-09-23"])
        self.assertEqual(last, "2027-03-22")

    def test_end_of_month_terms(self):
        dues, last = rules.rebase_due_dates([EOM_60], "2026-09-23")
        self.assertEqual(last, rules.term_due_date("2026-09-23", EOM_60))

    def test_nothing_to_do_without_a_bill_date_or_rows(self):
        self.assertEqual(rules.rebase_due_dates([DAYS_180], None), (None, None))
        self.assertEqual(rules.rebase_due_dates([], "2026-09-23"), (None, None))


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
        # 2 days after landing is 18/09, but 30 days from the 11/08 invoice date is 10/09: the earlier wins.
        self.assertEqual(rows[1]["due_date"], "2026-09-10")
        self.assertEqual(rows[0]["payment_term"], "180 Days")
        self.assertEqual(rows[1]["payment_term"], "Import Taxes")
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
        rows, _ = rules.import_schedule(100, 20, "2026-09-20", DAYS_180, "180 Days", "2026-10-01", 5)
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


class TestLocalInvoice(unittest.TestCase):
    def test_local_needs_bill_date_and_supplier_invoice_no(self):
        self.assertEqual(rules.pending_before_submit("Local Purchase", None, None, None, ""),
                         ["Supplier invoice date (bill date)", "Supplier invoice no."])
        self.assertEqual(rules.pending_before_submit("Local Purchase", "2026-08-04", None, None, "  "),
                         ["Supplier invoice no."])
        self.assertEqual(rules.pending_before_submit("Local Purchase", "2026-08-04", None, None, "KRACU/7025"), [])

    def test_import_still_needs_the_kra_details_not_the_invoice_no(self):
        self.assertEqual(rules.pending_before_submit("Importation", "2026-08-11", None, None, "3941"),
                         ["KRA customs entry date", "KRA customs entry number"])
        self.assertEqual(rules.pending_before_submit("Importation", "2026-08-11", "2026-09-27", "26MBA|M4", None), [])

    def test_local_schedule_is_one_row_due_per_terms(self):
        # ACC-PINV-2026-00520: 90 Days End of Month from 04-08-2026, KES 22,040
        t = {"due_date_based_on": "Day(s) after the end of the invoice month", "credit_days": 90, "credit_months": 0}
        rows, missing = rules.local_schedule(22040, "2026-08-04", t, "90 Days End of Month")
        self.assertEqual(missing, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["due_date"], "2026-11-29")
        self.assertEqual(rows[0]["invoice_portion"], 100.0)
        self.assertEqual(rows[0]["payment_amount"], 22040.0)
        self.assertEqual(rows[0]["payment_term"], "90 Days End of Month")

    def test_local_schedule_names_what_is_missing(self):
        rows, missing = rules.local_schedule(100, None, {"credit_days": 30}, "30 Days")
        self.assertIsNone(rows)
        self.assertEqual(missing, ["supplier invoice date (bill date)"])
        rows, missing = rules.local_schedule(100, "2026-08-04", None, None)
        self.assertEqual(missing, ["payment terms"])


class TestPendingLines(unittest.TestCase):
    # PUR-ORD-2026-00203 before it was received: four lines in Tonne, 98.66% billed
    ITEMS = [{"qty": 3, "received_qty": 0, "uom": "Tonne", "amount": 1680, "billed_amt": 1677.2},
             {"qty": 4, "received_qty": 0, "uom": "Tonne", "amount": 2240, "billed_amt": 2343.6},
             {"qty": 5, "received_qty": 0, "uom": "Tonne", "amount": 2800, "billed_amt": 2819.6},
             {"qty": 15, "received_qty": 14.638, "uom": "Tonne", "amount": 8400, "billed_amt": 8200.08}]

    def test_quantities_are_summed_per_unit_and_over_billing_never_offsets_another_line(self):
        p = rules.pending_lines(self.ITEMS)
        self.assertEqual(p["to_receive"], [{"uom": "Tonne", "qty": 12.362}])
        self.assertEqual(p["to_bill"], round(2.8 + 199.92, 2))   # lines 2 and 3 are over-billed: they add nothing

    def test_fully_done_order_has_nothing_pending(self):
        p = rules.pending_lines([{"qty": 2, "received_qty": 2, "uom": "Nos", "amount": 24300, "billed_amt": 24300}])
        self.assertEqual(p, {"to_receive": [], "to_bill": 0.0})

    def test_mixed_units_stay_separate(self):
        p = rules.pending_lines([{"qty": 5, "received_qty": 1, "uom": "Kg", "amount": 0, "billed_amt": 0},
                                 {"qty": 2, "received_qty": 0, "uom": "Roll", "amount": 0, "billed_amt": 0}])
        self.assertEqual({r["uom"]: r["qty"] for r in p["to_receive"]}, {"Kg": 4.0, "Roll": 2.0})

    def test_receive_from_invoice_says_why_not(self):
        self.assertEqual(rules.po_open_for_receipt(1, "To Receive and Bill", 0), (True, ""))
        self.assertEqual(rules.po_open_for_receipt(0, "Draft", 0), (False, "The PO is not approved"))
        self.assertEqual(rules.po_open_for_receipt(1, "Completed", 100), (False, "Already received"))
        self.assertEqual(rules.po_open_for_receipt(1, "Closed", 40), (False, "The PO is Closed"))


class TestPortAdditions(unittest.TestCase):
    def test_invoice_type_for_po(self):
        f = rules.invoice_type_for_po
        self.assertEqual(f("Import", None), "Importation")
        self.assertEqual(f("Local", "Importation"), "Importation")
        self.assertEqual(f(None, "Importation"), "Importation")
        self.assertEqual(f("Local", None), "Local Purchase")
        self.assertEqual(f(None, "In-State"), "Local Purchase")
        self.assertIsNone(f(None, None))
        self.assertIsNone(f("", ""))

    def test_plain_text(self):
        self.assertEqual(rules.plain_text('<div class="ql-editor read-mode"><p>One &amp; two</p><p><br></p><p>x&nbsp;&lt;3&gt; &quot;q&quot;</p></div>'),
                         'One & two\n\nx <3> "q"')
        self.assertEqual(rules.plain_text("a<br>b<br/>c"), "a\nb\nc")
        self.assertEqual(rules.plain_text("<ul><li>a</li><li>b</li></ul>"), "a\nb")
        self.assertEqual(rules.plain_text("<p>a</p><p></p><p></p><p></p><p>b</p>"), "a\n\nb")
        self.assertEqual(rules.plain_text(None), "")
        self.assertEqual(rules.plain_text("&amp;lt;"), "&lt;")

    def test_html_from_text(self):
        self.assertEqual(rules.html_from_text("a <b>\n\nc & d"),
                         '<div class="ql-editor read-mode"><p>a &lt;b&gt;</p><p><br></p><p>c &amp; d</p></div>')
        self.assertEqual(rules.html_from_text(""), '<div class="ql-editor read-mode"><p><br></p></div>')

    def test_text_round_trips(self):
        t = "Line one & <two>\n\nLine 'three'"
        self.assertEqual(rules.plain_text(rules.html_from_text(t)), t)

    def test_is_stale(self):
        self.assertTrue(rules.is_stale("2026-06-01", "2026-10-01"))
        self.assertFalse(rules.is_stale("2026-07-03", "2026-10-01"))   # exactly 90 days
        self.assertTrue(rules.is_stale("2026-07-02", "2026-10-01"))    # 91 days
        self.assertFalse(rules.is_stale("2026-10-05", "2026-10-01"))
        self.assertTrue(rules.is_stale("2026-06-01", "2026-10-01", days=30))
        self.assertFalse(rules.is_stale(None, "2026-10-01"))

    def test_order_date_check(self):
        rules.check_order_date("2026-10-01", "2026-10-01")
        rules.check_order_date(None, "2026-10-01")
        with self.assertRaises(rules.RuleError):
            rules.check_order_date("2026-10-02", "2026-10-01")

    def test_first_line_text_and_ordered_summary(self):
        self.assertEqual(rules.first_line_text("PAPER", "<p>Paper  roll\n80gsm</p>"), "Paper roll 80gsm")
        self.assertEqual(rules.first_line_text("PAPER", "<p>PAPER</p>"), "PAPER")
        self.assertEqual(rules.first_line_text("PAPER", None), "PAPER")
        self.assertEqual(rules.ordered_summary([]), {"text": "", "more": 0})
        self.assertEqual(rules.ordered_summary([{"item_name": "A", "description": ""}, {"item_name": "B"}, {"item_name": "C"}]),
                         {"text": "A", "more": 2})

    def test_rate_after_lookup(self):
        self.assertEqual(rules.rate_after_lookup(1, 129.4), 129.4)
        self.assertEqual(rules.rate_after_lookup(1, 0), 1.0)
        self.assertEqual(rules.rate_after_lookup(128, None), 128.0)


if __name__ == "__main__":
    unittest.main(verbosity=1)


class QboBillRules(unittest.TestCase):
    def test_import_uses_the_kra_entry_number(self):
        self.assertEqual(rules.qbo_docnumber("Importation", "26MBAIM406532497", "MPPAST2600557"), ("26MBAIM406532497", "KRA customs entry number"))

    def test_local_keeps_the_supplier_invoice_number(self):
        self.assertEqual(rules.qbo_docnumber("Local Purchase", "", "INV-9"), ("INV-9", "supplier invoice number"))

    def test_a_typed_number_wins(self):
        self.assertEqual(rules.qbo_docnumber("Importation", "KRA1", "S1", " TYPED ")[0], "TYPED")

    def test_an_import_without_a_kra_number_has_no_bill_number(self):
        n, _ = rules.qbo_docnumber("Importation", "", "S1")
        self.assertEqual(n, "")
        c = rules.qbo_checks(n, "2026-09-28", {"name": "V", "approved": 1}, [{"amount": 10, "account": "A"}], 10, "NEW")
        self.assertFalse(c[0]["ok"])

    def test_checks_all_pass_when_the_row_is_complete(self):
        c = rules.qbo_checks("N1", "2026-09-28", {"name": "V", "approved": 1}, [{"amount": 6, "item": "I"}, {"amount": 4, "account": "A"}], 10, "NEW")
        self.assertTrue(all(x["ok"] for x in c), c)

    def test_an_empty_or_unrouted_row_fails(self):
        self.assertFalse(all(x["ok"] for x in rules.qbo_checks("N1", "2026-09-28", {"name": "V", "approved": 1}, [], 10, "")))
        c = rules.qbo_checks("N1", "2026-09-28", {"name": "V", "approved": 1}, [{"amount": 10}], 10, "NEW")
        self.assertFalse(c[3]["ok"])
        self.assertIn("line 1", c[3]["why"])

    def test_vendor_must_be_mapped_and_approved_and_totals_must_match(self):
        self.assertFalse(rules.qbo_checks("N1", "d", None, [{"amount": 10, "item": "I"}], 10, "NEW")[2]["ok"])
        self.assertFalse(rules.qbo_checks("N1", "d", {"name": "V", "approved": 0}, [{"amount": 10, "item": "I"}], 10, "NEW")[2]["ok"])
        self.assertFalse(rules.qbo_checks("N1", "d", {"name": "V", "approved": 1}, [{"amount": 9, "item": "I"}], 10, "NEW")[4]["ok"])

    def test_too_long_a_number_fails(self):
        self.assertFalse(rules.qbo_checks("X" * 22, "d", {"name": "V", "approved": 1}, [{"amount": 1, "item": "I"}], 1, "NEW")[0]["ok"])


class QboTxnDate(unittest.TestCase):
    def test_an_import_is_dated_on_the_kra_entry_date(self):
        self.assertEqual(rules.qbo_txn_date("Importation", "2026-09-28", "2026-08-27", "2026-09-28"), "2026-09-28")

    def test_an_import_without_the_entry_date_has_no_date(self):
        self.assertIsNone(rules.qbo_txn_date("Importation", None, "2026-08-27", "2026-09-28"))

    def test_a_local_purchase_keeps_the_supplier_bill_date(self):
        self.assertEqual(rules.qbo_txn_date("Local Purchase", "2026-09-28", "2026-08-27", "2026-09-28"), "2026-08-27")
        self.assertEqual(rules.qbo_txn_date("Local Purchase", None, None, "2026-09-28"), "2026-09-28")


class BillDateReview(unittest.TestCase):
    def test_same_month_late_posting_is_accepted(self):
        r = rules.bill_date_review("27/10/2026", "2026-10-31")
        self.assertEqual((r["state"], r["ok"], r["review"]), ("same_month", True, False))
        self.assertEqual(r["kra_date"], "2026-10-27")

    def test_cross_month_needs_review_even_a_day_apart(self):
        r = rules.bill_date_review("2026-10-31", "2026-11-01", ["Purchase Manager"])
        self.assertEqual((r["state"], r["ok"], r["review"]), ("cross_month", False, True))

    def test_same_month_number_other_year_is_cross_month(self):
        self.assertEqual(rules.bill_date_review("2025-10-27", "2026-10-28")["state"], "cross_month")

    def test_same_day_and_earlier(self):
        self.assertEqual(rules.bill_date_review("2026-10-27", "2026-10-27")["state"], "same_day")
        r = rules.bill_date_review("2026-10-27", "2026-10-20", ["Purchase Manager"])
        self.assertEqual((r["state"], r["fix"], r["fix_date"]), ("earlier", True, "2026-10-27"))

    def test_missing_or_unreadable_kra_date_stays_unverified(self):
        for k in (None, "", "not a date"):
            r = rules.bill_date_review(k, "2026-10-31")
            self.assertEqual((r["state"], r["ok"], r["review"]), ("unverified", False, False))

    def test_december_to_january_is_cross_month(self):
        self.assertEqual(rules.bill_date_review("31/12/2026", "2027-01-02")["state"], "cross_month")

    def test_same_day_number_in_another_year_is_cross_month(self):
        self.assertEqual(rules.bill_date_review("27/10/2025", "2026-10-27")["state"], "cross_month")

    def test_impossible_kra_dates_are_unverified(self):
        for k in ("31/02/2026", "2026-13-01", "00/10/2026"):
            self.assertEqual(rules.bill_date_review(k, "2026-10-31")["state"], "unverified", k)

    def test_kra_formats(self):
        for k in ("2026-10-27", "27/10/2026", "27-10-2026", "27.10.2026", "27-Oct-2026", "2026-10-27T00:00:00", "27/10/2026 10:15:00"):
            self.assertEqual(rules.loose_date(k).isoformat(), "2026-10-27", k)
        self.assertEqual(rules.loose_date(" 27/10/2026 ").isoformat(), "2026-10-27")
        self.assertEqual(rules.loose_date("7-1-2026").isoformat(), "2026-01-07")
        self.assertIsNone(rules.loose_date("rubbish"))

    def test_dmy(self):
        self.assertEqual(rules.dmy("2026-10-27"), "27/10/2026")
        self.assertEqual(rules.dmy(None), "")

    def test_a_queue_row_without_a_category_reads_staged_not_not_staged(self):
        c = rules.qbo_checks("N1", "2026-10-31", {"name": "V", "approved": 1}, [{"amount": 1, "item": "I"}], 1, None)
        self.assertEqual(c[5]["why"], "staged")
        self.assertNotIn("not staged", " ".join(x["why"] for x in c))


class StageHint(unittest.TestCase):
    def test_off_cutoff_and_unexplained(self):
        self.assertIn("switched off", rules.stage_hint(0, "2026-04-01", "2026-10-31"))
        self.assertIn("before the QuickBooks cutoff 01/04/2026", rules.stage_hint(1, "2026-04-01", "2026-03-31"))
        self.assertIn("Error Log", rules.stage_hint(1, "2026-04-01", "2026-10-31"))


class ReceiptCover(unittest.TestCase):
    L = {"item": "TAPE", "bill_qty": 2160, "po_qty": 2160}

    def test_recorded_receipts_cover_or_fall_short(self):
        ok = rules.receipt_cover([{**self.L, "received": 2160, "billed_before": 0}], 0)
        self.assertEqual((ok["mode"], ok["ok"], ok["short"]), ("receipts", True, 0))
        short = rules.receipt_cover([{**self.L, "received": 0, "billed_before": 0}], 0)
        self.assertEqual((short["ok"], short["lines"][0]["short"]), (False, 2160))
        self.assertIn("not fully received", short["text"])

    def test_a_second_part_bill_needs_receipts_for_both_parts(self):
        r = rules.receipt_cover([{**self.L, "bill_qty": 1000, "received": 1500, "billed_before": 1000}], 0)
        self.assertEqual(r["lines"][0]["short"], 500)

    def test_receiving_through_the_invoice_flags_goods_already_received(self):
        clean = rules.receipt_cover([{**self.L, "received": 0, "billed_before": 0}], 1)
        self.assertEqual((clean["mode"], clean["ok"]), ("invoice", True))
        twice = rules.receipt_cover([{**self.L, "received": 2160, "billed_before": 0}], 1)
        self.assertEqual((twice["ok"], twice["lines"][0]["overlap"]), (False, 2160))
        self.assertIn("twice", twice["text"])

    def test_no_po_lines(self):
        self.assertTrue(rules.receipt_cover([], 0)["ok"])

    def test_policy_lives_in_one_constant(self):
        self.assertEqual(rules.DATE_RULE_POLICY["cross_month"], ("review", "block"))
        loose = {**rules.DATE_RULE_POLICY, "cross_month": ("accept", "accept")}
        self.assertTrue(rules.bill_date_review("2026-10-31", "2026-11-01", ["Purchase User"], loose)["ok"])


class BilledQty(unittest.TestCase):
    def test_units_from_amount(self):
        self.assertEqual(rules.billed_qty(43000, 43, 2160), 1000)
        self.assertEqual(rules.billed_qty(0, 43, 2160), 0)
        self.assertEqual(rules.billed_qty(999999, 43, 2160), 2160)
        self.assertEqual(rules.billed_qty(10, 0, 5), 0)


class MappingHistory(unittest.TestCase):
    P = [{"name": "Q3", "pi": "PI-3", "date": "2026-10-20", "taxcode": "8"}, {"name": "Q2", "pi": "PI-2", "date": "2026-09-01", "taxcode": "8"},
         {"name": "Q1", "pi": "PI-1", "date": "2026-08-01", "taxcode": "8"}, {"name": "Q0", "pi": "PI-0", "date": "2026-07-01", "taxcode": "8"}]

    def test_newest_three_per_item_one_per_invoice_inactive_left_out(self):
        L = [{"parent": "Q1", "erp_item_code": "TAPE", "account_id": "A1", "account_name": "Purchase Trading"},
             {"parent": "Q3", "erp_item_code": "TAPE", "account_id": "A1", "account_name": "Purchase Trading"},
             {"parent": "Q3", "erp_item_code": "TAPE", "account_id": "A1", "account_name": "Purchase Trading"},
             {"parent": "Q2", "erp_item_code": "TAPE", "item_id": "I9", "item_name": "Old item"},
             {"parent": "Q0", "erp_item_code": "TAPE", "account_id": "A2", "account_name": "Printing"},
             {"parent": "Q2", "erp_item_code": "BOX", "account_id": "A1"},
             {"parent": "QX", "erp_item_code": "TAPE", "account_id": "A1"}]
        h = rules.mapping_history(self.P, L, ["TAPE", "GLUE"], active=lambda kind, i: i != "I9")
        self.assertEqual([(x["queue"], x["route"], x["dest_name"]) for x in h["TAPE"]],
                         [("Q3", "Account", "Purchase Trading"), ("Q1", "Account", "Purchase Trading"), ("Q0", "Account", "Printing")])
        self.assertEqual(h["GLUE"], [])
        self.assertNotIn("BOX", h)
        self.assertEqual(h["TAPE"][0]["date"], "2026-10-20")


class DocSearch(unittest.TestCase):
    D = [
        {"doctype": "Purchase Order", "name": "PUR-ORD-2026-00264", "refs": ["PUR-ORD-2026-00264"], "created": "2026-10-22 09:00",
         "fields": {"supplier": "ROK INDUSTRIES LIMITED"}, "items": [{"code": "TAPE-24", "text": "Printed Cello Tape - 24mm Cello tape printed (Cereal Box Ltd)"}]},
        {"doctype": "Purchase Invoice", "name": "ACC-PINV-2026-00673", "refs": ["ACC-PINV-2026-00673", "0430146720000082785"], "created": "2026-10-31 10:00",
         "fields": {"supplier": "ROK INDUSTRIES LIMITED"}, "items": [{"code": "TAPE-24", "text": "Printed Cello Tape - 24mm"}]},
        {"doctype": "Material Request", "name": "MAT-MR-2026-00010", "refs": ["MAT-MR-2026-00010"], "created": "2026-10-01 10:00",
         "fields": {}, "items": [{"code": "GLUE-1", "text": "Hotmelt glue"}]},
    ]

    def test_exact_reference_first(self):
        r = rules.doc_search("pur-ord-2026-00264", self.D)
        self.assertEqual((r[0]["name"], r[0]["match"]), ("PUR-ORD-2026-00264", "reference"))
        self.assertEqual(rules.doc_search("0430146720000082785", self.D)[0]["name"], "ACC-PINV-2026-00673")

    def test_item_description_and_supplier(self):
        self.assertEqual([x["name"] for x in rules.doc_search("cereal box", self.D)], ["PUR-ORD-2026-00264"])
        self.assertEqual([x["name"] for x in rules.doc_search("rok", self.D)], ["ACC-PINV-2026-00673", "PUR-ORD-2026-00264"])

    def test_item_code_exact_ranks_above_text(self):
        r = rules.doc_search("tape-24", self.D)
        self.assertEqual({x["match"] for x in r}, {"item code"})

    def test_one_typo_still_finds_it_but_below_plain(self):
        r = rules.doc_search("cereel box", self.D)
        self.assertEqual([x["name"] for x in r], ["PUR-ORD-2026-00264"])
        self.assertTrue(r[0]["match"].startswith("near spelling"))
        self.assertEqual(rules.doc_search("hotmelt", self.D)[0]["score"], 60)

    def test_every_word_must_match(self):
        self.assertEqual(rules.doc_search("rok glue", self.D), [])
        self.assertEqual(rules.doc_search("  ", self.D), [])


class CancelCheck(unittest.TestCase):
    def test_a_clean_unpaid_unpushed_invoice_can_be_cancelled(self):
        r = rules.cancel_check(1, [], [], 0, None, None)
        self.assertEqual((r["blockers"], r["needs_qbo_ack"]), ([], False))

    def test_blockers(self):
        self.assertIn("draft", rules.cancel_check(0, [], [], None, None, None)["blockers"][0])
        self.assertIn("ACC-PAY-1", rules.cancel_check(1, ["ACC-PAY-1"], [], None, None, None)["blockers"][0])
        self.assertIn("MAT-PRE-9", rules.cancel_check(1, [], ["MAT-PRE-9"], None, None, None)["blockers"][0])
        self.assertIn("has not answered", rules.cancel_check(1, [], [], 1, "Queued", None)["blockers"][0])

    def test_a_failed_push_does_not_block_and_a_synced_bill_needs_the_ack(self):
        self.assertEqual(rules.cancel_check(1, [], [], 1, "Failed", None)["blockers"], [])
        r = rules.cancel_check(1, [], [], 1, "Pushed", "1234")
        self.assertEqual((r["blockers"], r["needs_qbo_ack"]), ([], True))
        self.assertIn("1234", r["warnings"][0])


class DatePolicyByRole(unittest.TestCase):
    """Tanuj's answers of 31/10/2026 to (a)-(c)."""
    U, M = ["Purchase User"], ["Purchase User", "Purchase Manager"]

    def test_a_cross_month_blocks_a_purchase_user_and_asks_a_manager_for_the_tick(self):
        u = rules.bill_date_review("2026-10-31", "2026-11-01", self.U)
        self.assertEqual((u["block"], u["review"]), (True, False))
        self.assertIn("ask a Purchase Manager", u["text"])
        m = rules.bill_date_review("2026-10-31", "2026-11-01", self.M)
        self.assertEqual((m["block"], m["review"]), (False, True))

    def test_b_an_earlier_bill_date_must_become_kras_date_for_everyone(self):
        for roles in (self.U, self.M, ["System Manager"]):
            r = rules.bill_date_review("27/10/2026", "2026-10-20", roles)
            self.assertEqual((r["fix"], r["fix_date"], r["review"], r["ok"]), (True, "2026-10-27", False, False))
            self.assertIn("27/10/2026", r["text"])

    def test_c_no_kra_date_only_a_manager_pushes_unverified(self):
        u = rules.bill_date_review(None, "2026-10-31", self.U)
        self.assertEqual((u["block"], u["unverified"]), (True, False))
        self.assertIn("Only a Purchase Manager", u["text"])
        m = rules.bill_date_review(None, "2026-10-31", self.M)
        self.assertEqual((m["block"], m["unverified"], m["ok"]), (False, True, False))

    def test_accepted_cases_are_the_same_for_everyone(self):
        for roles in (self.U, self.M):
            self.assertTrue(rules.bill_date_review("27/10/2026", "2026-10-31", roles)["ok"])
            self.assertTrue(rules.bill_date_review("31/10/2026", "2026-10-31", roles)["ok"])

    def test_system_manager_counts_as_manager_and_no_roles_as_user(self):
        self.assertTrue(rules.is_date_manager(["System Manager"]))
        self.assertTrue(rules.bill_date_review("2026-10-31", "2026-11-01", None)["block"])


class ReceiptRefusal(unittest.TestCase):
    def test_d_names_the_receipt_and_the_goods(self):
        cover = rules.receipt_cover([{"item": "Tape", "bill_qty": 2160, "po_qty": 2160, "received": 2160, "billed_before": 0, "uom": "Nos"}], 1)
        why = rules.receipt_refusal(cover, ["MAT-PRE-2026-00099"])
        self.assertIn("MAT-PRE-2026-00099", why)
        self.assertIn("Tape: 2160 Nos", why)

    def test_nothing_received_or_ledger_only_is_not_refused(self):
        self.assertEqual(rules.receipt_refusal(rules.receipt_cover([{"item": "Tape", "bill_qty": 5, "received": 0}], 1), []), "")
        self.assertEqual(rules.receipt_refusal(rules.receipt_cover([{"item": "Tape", "bill_qty": 5, "received": 5}], 0), ["X"]), "")
