"""Import cost rules, tested without a bench: customs entry, receiving lock, landed cost, deposit.

    python3 -m unittest vcl_procurement.tests.test_import_rules
"""
import sys
import unittest

sys.path.insert(0, __file__.rsplit("/vcl_procurement/tests/", 1)[0])
from vcl_procurement.api import import_rules as R  # noqa: E402
from vcl_procurement.api.rules import RuleError  # noqa: E402

ACCOUNTS = {"vat": "VAT IN", "valuation": "EIV"}


def entry(**kw):
    base = {"entry_no": "26MBAIM401234567", "entry_date": "2026-11-20", "rate": "129.45", "duty": "349,515",
            "idf": "87379", "rdl": "69903", "mss": "", "vat": "615146", "paid_from": "KCB"}
    base.update(kw)
    return R.clean_entry(base, "USD")


class Mode(unittest.TestCase):
    def test_absent_or_zero_is_off(self):
        for v in (None, 0, "0", "", "no"):
            self.assertEqual(R.mode(v), R.OFF)

    def test_one_posts(self):
        for v in (1, "1", True, "post"):
            self.assertEqual(R.mode(v), R.POST)

    def test_draft_posts_only_for_named_invoices(self):
        self.assertEqual(R.mode("draft"), R.DRAFT)
        self.assertEqual(R.mode("draft", ["PINV-1"], "PINV-2"), R.DRAFT)
        self.assertEqual(R.mode("draft", ["PINV-1"], "PINV-1"), R.POST)


class Amounts(unittest.TestCase):
    def test_loose_input(self):
        self.assertEqual(R.amount(" 1,121,943.50 "), 1121943.5)
        self.assertEqual(R.amount("KES 2 000"), 2000.0)
        self.assertEqual(R.amount(""), 0.0)
        self.assertEqual(R.amount(None), 0.0)

    def test_refuses_words_and_negatives(self):
        for bad in ("abc", "-5", "nan", "inf"):
            with self.assertRaises(RuleError):
                R.amount(bad)


class CustomsEntry(unittest.TestCase):
    def test_clean(self):
        e = entry(entry_no=" 26mba im4012 ")
        self.assertEqual(e["entry_no"], "26MBAIM4012")
        self.assertEqual(e["rate"], 129.45)
        self.assertEqual(e["duty"], 349515.0)
        self.assertEqual(e["mss"], 0.0)

    def test_kes_invoice_has_rate_one(self):
        self.assertEqual(R.clean_entry({"rate": "129"}, "KES")["rate"], 1.0)

    def test_missing_named_in_words(self):
        e = R.clean_entry({}, "USD")
        m = R.entry_missing(e, [])
        self.assertEqual(len(m), 6)
        self.assertIn("KRA customs entry number", m)
        self.assertIn("container number on the invoice", m)
        self.assertEqual(R.entry_missing(entry(), ["MSKU4182730"]), [])

    def test_vat_alone_is_enough(self):
        e = entry(duty="", idf="", rdl="")
        self.assertEqual(R.entry_missing(e, ["MSKU4182730"]), [])

    def test_rate_warning_catches_a_slipped_digit(self):
        self.assertEqual(R.rate_warning(129.45, 129.0), "")
        self.assertTrue(R.rate_warning(12.945, 129.0))
        self.assertTrue(R.rate_warning(1294.5, 129.0))
        self.assertEqual(R.rate_warning(129.45, 0), "")

    def test_journal_balances_and_keeps_vat_out_of_valuation(self):
        e = entry()
        rows = R.journal_rows(e, ACCOUNTS, R.reference(e["entry_no"], "INV-88213", ["msku 418273 0"]))
        self.assertEqual(round(sum(r["debit"] for r in rows), 2), round(sum(r["credit"] for r in rows), 2))
        vat = [r for r in rows if r["account"] == "VAT IN"]
        self.assertEqual([r["debit"] for r in vat], [615146.0])
        self.assertEqual(sum(r["debit"] for r in rows if r["account"] == "EIV"), 349515 + 87379 + 69903)
        self.assertEqual(rows[-1], {"account": "KCB", "debit": 0.0, "credit": 1121943.0,
                                    "remark": "KRA customs entry · Entry 26MBAIM401234567 · Inv INV-88213 · MSKU 418273 0"})
        self.assertTrue(all("26MBAIM401234567" in r["remark"] for r in rows))

    def test_zero_levies_are_left_out(self):
        rows = R.journal_rows(entry(mss="0", rdl=""), ACCOUNTS, "Entry X")
        self.assertEqual(len(rows), 4)

    def test_unset_account_refuses_in_words(self):
        with self.assertRaises(RuleError) as cm:
            R.journal_rows(entry(), {"vat": None, "valuation": "EIV"}, "Entry X")
        self.assertIn("vcl_import_vat_account", str(cm.exception))
        with self.assertRaises(RuleError):
            R.journal_rows(entry(), {"vat": "VAT IN", "valuation": None}, "Entry X")

    def test_schedule_collapses_only_when_taxes_row_is_zero(self):
        rows = [{"payment_term": "120 days", "due_date": "2027-03-01", "invoice_portion": 80, "payment_amount": 27000},
                {"payment_term": "Import Taxes", "due_date": "2026-12-01", "invoice_portion": 20, "payment_amount": 0}]
        one = R.schedule_without_taxes(rows, 27000)
        self.assertEqual(one, [{"payment_term": "120 days", "due_date": "2027-03-01", "invoice_portion": 100.0, "payment_amount": 27000.0}])
        rows[1]["payment_amount"] = 500
        self.assertIsNone(R.schedule_without_taxes(rows, 27500))
        self.assertIsNone(R.schedule_without_taxes(rows[:1], 27000))


class ReceivingLock(unittest.TestCase):
    def test_off_and_draft_sites_never_lock(self):
        for m in (R.OFF, R.DRAFT):
            self.assertEqual(R.receiving_refusal(m, "Import", []), "")

    def test_local_orders_never_lock(self):
        self.assertEqual(R.receiving_refusal(R.POST, "Local", []), "")
        self.assertEqual(R.receiving_refusal(R.POST, None, []), "")

    def test_import_without_invoice_is_locked(self):
        self.assertIn("no supplier invoice", R.receiving_refusal(R.POST, "Import", []))

    def test_draft_invoice_is_locked_and_named(self):
        why = R.receiving_refusal(R.POST, "Import", [{"name": "ACC-PINV-2026-00700", "docstatus": 0, "customs_entry": False}])
        self.assertIn("ACC-PINV-2026-00700 is still a draft", why)

    def test_submitted_without_customs_entry_is_locked(self):
        why = R.receiving_refusal(R.POST, "Import", [{"name": "P1", "docstatus": 1, "customs_entry": False}])
        self.assertIn("no posted KRA customs entry", why)

    def test_submitted_on_its_entry_opens(self):
        inv = [{"name": "P1", "docstatus": 1, "customs_entry": True}, {"name": "P0", "docstatus": 2, "customs_entry": False}]
        self.assertEqual(R.receiving_refusal(R.POST, "import", inv), "")

    def test_cancelled_invoices_do_not_count(self):
        self.assertIn("no supplier invoice", R.receiving_refusal(R.POST, "Import", [{"name": "P0", "docstatus": 2}]))


class Gate(unittest.TestCase):
    def test_container_matched_loosely(self):
        self.assertTrue(R.gate_check("msku-418273 0", None, ["MSKU 4182730"])["ok"])

    def test_wrong_container_stops_unloading(self):
        g = R.gate_check("TCLU1111111", None, ["MSKU4182730"])
        self.assertFalse(g["ok"])
        self.assertIn("Do not unload", g["why"])

    def test_blank_container(self):
        self.assertFalse(R.gate_check(" ", None, ["MSKU4182730"])["ok"])

    def test_seal_checked_only_when_recorded(self):
        seals = {"MSKU4182730": "ML-7720145"}
        self.assertTrue(R.gate_check("MSKU4182730", "ml 7720145", ["MSKU4182730"], seals)["ok"])
        self.assertFalse(R.gate_check("MSKU4182730", "ML-999", ["MSKU4182730"], seals)["ok"])
        self.assertFalse(R.gate_check("MSKU4182730", "", ["MSKU4182730"], seals)["ok"])


class LandedCost(unittest.TestCase):
    SOURCES = [{"account": "EIV", "amount": 349515, "description": "Import Duty (customs entry)", "source": "JV-1"},
               {"account": "EIV", "amount": 87379, "description": "IDF (customs entry)", "source": "JV-1"},
               {"account": "5113", "amount": 185000, "description": "Shipping line SL-1", "source": "P-2"}]

    def test_first_voucher_carries_everything(self):
        rows = R.landed_merge(R.landed_pending(self.SOURCES, {}))
        self.assertEqual([(r["account"], r["amount"]) for r in rows], [("EIV", 436894.0), ("5113", 185000.0)])
        self.assertEqual(rows[0]["description"], "Import Duty (customs entry); IDF (customs entry)")

    def test_second_voucher_carries_only_what_came_later(self):
        later = self.SOURCES + [{"account": "5113", "amount": 210000, "description": "CFS C-9", "source": "P-3"},
                                {"account": "5113", "amount": 95000, "description": "Clearing H-4", "source": "P-4"}]
        rows = R.landed_merge(R.landed_pending(later, {"EIV": 436894, "5113": 185000}))
        self.assertEqual(rows, [{"account": "5113", "amount": 305000.0, "description": "CFS C-9; Clearing H-4"}])

    def test_fully_booked_is_empty(self):
        self.assertEqual(R.landed_pending(self.SOURCES, {"EIV": 436894, "5113": 185000}), [])

    def test_rounding_dust_is_not_a_cost(self):
        self.assertEqual(R.landed_pending(self.SOURCES[:1], {"EIV": 349514.6}), [])

    def test_partly_booked_source_keeps_its_rest(self):
        rows = R.landed_pending(self.SOURCES[:1], {"EIV": 300000})
        self.assertEqual(rows[0]["amount"], 49515.0)

    def test_per_kg(self):
        self.assertEqual(R.per_kg(691797, 15996), 43.25)
        self.assertIsNone(R.per_kg(100, 0))


class DepositAndClose(unittest.TestCase):
    def test_recovery_within_balance(self):
        self.assertEqual(R.deposit_recovery(150000, "140,000", "10000"), (140000.0, 10000.0))

    def test_recovery_over_balance_refused(self):
        with self.assertRaises(RuleError):
            R.deposit_recovery(150000, 150000, 1)

    def test_nothing_entered_refused(self):
        with self.assertRaises(RuleError):
            R.deposit_recovery(150000, "", "")

    def test_close_lists_what_is_waiting_in_order(self):
        s = R.close_state(27000, 2, 150000, 305000, True)
        self.assertFalse(s["can_close"])
        self.assertEqual(len(s["waiting"]), 4)
        self.assertTrue(s["waiting"][0].startswith("KES 305,000.00"))
        self.assertEqual(s["waiting"][-1], "Supplier not fully paid")

    def test_close_when_everything_is_zero(self):
        self.assertEqual(R.close_state(0, 0, 0, 0.4, True), {"can_close": True, "waiting": []})

    def test_not_received_keeps_it_open(self):
        self.assertEqual(R.close_state(0, 0, 0, 0, False)["waiting"], ["Goods not received"])


if __name__ == "__main__":
    unittest.main()
