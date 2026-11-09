import unittest
from vcl_procurement.api import rules

class PaymentParity(unittest.TestCase):
    def test_D2_balances_are_in_invoice_currency(self):
        self.assertEqual(rules.invoice_currency_amount(129000, "USD", "KES", 129), 1000)
        self.assertEqual(rules.invoice_currency_amount(1000, "USD", "USD", 129), 1000)
        self.assertEqual(rules.invoice_currency_amount(1000, "KES", "KES", 1), 1000)
        with self.assertRaises(rules.RuleError):
            rules.invoice_currency_amount(1000, "USD", "EUR", 129)

    def test_D1_every_posting_choice_participates_in_deduplication(self):
        original = dict(company="VCL", party_type="Supplier", party="S", payment_type="Pay",
            paid_from="Bank A", paid_to="Creditors", paid_from_account_currency="KES", paid_to_account_currency="USD",
            posting_date="2026-11-09", reference_no="X", reference_date="2026-11-09", mode_of_payment="Bank",
            remarks="Invoice", paid_amount=1290, received_amount=10, source_exchange_rate=1, target_exchange_rate=129,
            references=[dict(reference_doctype="Purchase Invoice", reference_name="PI-1", payment_term="Net", allocated_amount=10)])
        identity = rules.payment_identity(original)
        self.assertEqual(identity, rules.payment_identity(dict(original)))
        for key, value in original.items():
            changed = dict(original)
            changed[key] = ([dict(value[0], allocated_amount=9)] if key == "references" else
                            value + 1 if isinstance(value, (int, float)) else value + " different")
            self.assertNotEqual(identity, rules.payment_identity(changed), key)

    def test_TRK_00023_actual_rows_require_an_explicit_finite_amount(self):
        for missing in [None, "", float("nan"), float("inf")]:
            with self.assertRaises(rules.RuleError):
                rules.check_tax_rows([{"charge_type": "Actual", "kes": missing}])
        rules.check_tax_rows([{"charge_type": "Actual", "kes": 0}])

    def test_A10_no_PO_invoice_requires_reference_and_real_expense_lines(self):
        bill = {"supplier": "S", "bill_no": "X", "bill_date": "2026-11-09", "lines": [{"description": "Repairs", "account": "Repairs", "qty": 1, "rate": 100}]}
        self.assertIsNone(rules.expense_bill_error(bill))
        for key in ("supplier", "bill_no", "bill_date", "lines"):
            self.assertTrue(rules.expense_bill_error(dict(bill, **{key: None})))
        self.assertTrue(rules.expense_bill_error(dict(bill, invoice_type="Importation")))

    def test_A12_A13_D18_push_readiness(self):
        queue = dict(qbo_txn_type="Expense", vat_line_manual=1, lines=[dict(amount=100)])
        self.assertGreater(len(rules.qbo_payment_errors(queue, 116)), 3)
        queue.update(pay_bank_account="Bank", pay_cheque_no="CH1", pay_date="2026-11-09", pay_amount=116,
                     vat_line_account="VAT", vat_line_amount=16)
        self.assertEqual(rules.qbo_payment_errors(queue, 116), [])
        queue["pay_amount"] = 115
        self.assertIn("cheque amount", rules.qbo_payment_errors(queue, 116)[0])
