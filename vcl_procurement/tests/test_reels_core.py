import unittest
from vcl_procurement.reels_core import clean_reel, receipt_conversion

class ReelQuantities(unittest.TestCase):
    def test_supplier_gross_is_not_reported_as_measured_net(self):
        r=clean_reel(dict(weight_basis='Supplier-declared weight',declared_weight=1601,gross_weight=1601))
        self.assertEqual(r['stock_qty'],1601)
        self.assertEqual(r['net_weight'],0)
        self.assertEqual(r['verification_status'],'Pending verification')

    def test_measured_net_excludes_core(self):
        self.assertEqual(clean_reel(dict(gross_weight=520,core_weight=20))['stock_qty'],500)

    def test_negative_nonfinite_and_inconsistent_values_are_rejected(self):
        for raw in ({'net_weight':float('inf')},{'net_weight':float('nan')},
                    {'net_weight':10,'core_weight':-1},{'gross_weight':20,'core_weight':21},
                    {'gross_weight':20,'core_weight':2,'net_weight':19}):
            with self.subTest(raw=raw),self.assertRaises(ValueError): clean_reel(raw)

    def test_only_fixed_mass_conversions_are_accepted(self):
        self.assertEqual(receipt_conversion('Tonne','Kg',1000),1000)
        self.assertEqual(receipt_conversion('Kg','Kg',1),1)
        for uom,factor in [('Tonne',1),('REEL',1000),('Kg',1000)]:
            with self.subTest(uom=uom),self.assertRaises(ValueError): receipt_conversion(uom,'Kg',factor)

if __name__=='__main__': unittest.main()
