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
