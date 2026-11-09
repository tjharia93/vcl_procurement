import unittest
from unittest.mock import patch
from types import SimpleNamespace

class Doc(SimpleNamespace):
    def get(self, k, default=None): return getattr(self, k, default)
    def set(self, k, value): setattr(self, k, value)

from vcl_procurement.tests.test_port_api import pi, frappe, Row, _Thrown

class ParityGates(unittest.TestCase):
    def test_D4_linked_order_type_is_normalized_before_gate(self):
        doc = Doc(docstatus=0, items=[Row(purchase_order='PO')], custom_purchase_invoice_type='Local Purchase')
        with patch.object(pi, '_po_derived_type', return_value='Importation'):
            missing = pi._missing(doc)
        self.assertEqual(doc.custom_purchase_invoice_type, 'Importation')
        self.assertIn('KRA customs entry date', missing)

    def test_D5_customs_posting_date_is_enforced_at_submit(self):
        doc = Row(docstatus=0, custom_purchase_invoice_type='Importation', custom_kra_entry_date='2026-11-01', posting_date='2026-11-02')
        with self.assertRaisesRegex(_Thrown, 'posting date equal'):
            pi.gate_before_submit(doc)

    def test_D9_final_workspace_date_controls_fx(self):
        doc = Doc(items=[Row(name='x', po_detail='d', qty=1, rate=5)], custom_purchase_invoice_type='Local Purchase')
        with patch.object(pi, '_refresh_rate') as fx:
            pi._shape_bill(doc, {'posting_date': '2026-10-15'})
        fx.assert_called_once_with(doc, '2026-10-15')
