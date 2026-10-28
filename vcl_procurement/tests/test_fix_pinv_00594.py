import unittest

from vcl_procurement.patches import fix_pinv_00594_schedule as fix


class TestFix00594(unittest.TestCase):
    def test_payment_fills_taxes_first_and_the_rest_goes_to_net(self):
        net, tax = fix.corrected_rows(41529.0486, 8949.9214)
        self.assertEqual(tax["paid_amount"], 8949.9214)
        self.assertEqual(tax["outstanding"], 0)
        self.assertAlmostEqual(net["paid_amount"], 0.6886, 4)
        self.assertAlmostEqual(net["outstanding"], 41528.36, 4)

    def test_rows_add_up_to_the_invoice_balance(self):
        net, tax = fix.corrected_rows(41529.0486, 8949.9214)
        self.assertAlmostEqual(net["outstanding"] + tax["outstanding"], fix.OUTSTANDING, 2)

    def test_base_amounts_use_the_invoice_rate(self):
        net, tax = fix.corrected_rows(41529.0486, 8949.9214)
        self.assertAlmostEqual(tax["base_paid_amount"], 8949.9214 * 129.61, 3)
        self.assertEqual(tax["base_outstanding"], 0)
        self.assertAlmostEqual(net["base_outstanding"], 41528.36 * 129.61, 2)


if __name__ == "__main__":
    unittest.main()
