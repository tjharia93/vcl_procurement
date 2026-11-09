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

class Permissions(unittest.TestCase):
    def test_D11_register_headers_use_permission_aware_queries(self):
        from vcl_procurement.api import landing
        calls = []
        def scoped(dt, **kwargs):
            calls.append(dt)
            return []
        with patch.object(frappe, 'get_list', scoped, create=True), patch.object(frappe, 'get_meta', lambda dt: Doc(has_field=lambda f: False), create=True), patch.object(frappe, 'get_all', side_effect=AssertionError('unscoped header'), create=True):
            self.assertEqual(landing.mr_list(), [])
            self.assertEqual(landing.pr_list(), [])
            self.assertEqual(landing.pi_list(), [])
        self.assertEqual(calls, ['Material Request', 'Purchase Receipt', 'Purchase Invoice'])

    def test_D11_unreadable_container_parent_never_becomes_a_hit(self):
        from vcl_procurement.api import landing
        with patch.object(frappe, 'get_all', lambda *a, **k: [Row(parent='hidden', container_reference='MSCU12345', container_size='40 FT')], create=True), patch.object(frappe, 'get_list', lambda *a, **k: [], create=True), patch.object(landing, '_shipment_hits', return_value=[]):
            self.assertEqual(landing.container_search('MSCU12345'), [])
