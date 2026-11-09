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

class MutableDoc(Doc):
    def update(self, data):
        for key, value in data.items(): setattr(self, key, value)
    def as_dict(self): return vars(self).copy()
    def check_permission(self, *args): pass
    def save(self): self.saved = True
    def insert(self): self.inserted = True

class PaymentWiring(unittest.TestCase):
    def test_D1_old_bank_draft_is_not_reused(self):
        wanted = MutableDoc(name='wanted', paid_from='Bank B', paid_amount=100, reference_no=None, references=[])
        other = MutableDoc(name='old', paid_from='Bank A', paid_amount=100, reference_no=None, references=[])
        same = MutableDoc(**{**vars(wanted), 'name': 'same'})
        with patch.object(frappe.db, 'sql', return_value=[('old',), ('same',)], create=True), patch.object(frappe, 'get_doc', lambda dt, n: {'old': other, 'same': same}[n], create=True), patch.object(frappe, 'has_permission', return_value=True, create=True):
            self.assertEqual(pi._recent_duplicate('PI', wanted), 'same')
        with patch.object(frappe.db, 'sql', return_value=[('old',)], create=True), patch.object(frappe, 'get_doc', return_value=other, create=True), patch.object(frappe, 'has_permission', return_value=True, create=True):
            self.assertIsNone(pi._recent_duplicate('PI', wanted))

    def test_D2_payment_mapper_gets_party_currency_not_invoice_currency(self):
        import sys, types
        from unittest.mock import Mock
        module = types.ModuleType('erpnext.accounts.doctype.payment_entry.payment_entry')
        payment = MutableDoc(paid_from_account_currency='KES', paid_to_account_currency='KES', references=[], source_exchange_rate=1, received_amount=13000, paid_amount=13000)
        module.get_payment_entry = Mock(return_value=payment)
        doc = MutableDoc(name='PI', currency='USD', party_account_currency='KES', conversion_rate=130, outstanding_amount=13000)
        with patch.dict(sys.modules, {'erpnext.accounts.doctype.payment_entry.payment_entry': module}), patch.object(frappe.db, 'get_value', return_value='KES'):
            _, applied = pi._build_payment(doc, {'account': 'Bank', 'amount': 13000, 'rate': 130, 'date': '2026-11-09'})
            self.assertEqual(applied, 100)
            self.assertEqual(module.get_payment_entry.call_args.kwargs['party_amount'], 13000)
            with self.assertRaisesRegex(_Thrown, 'exchange rate greater than zero'):
                pi._build_payment(doc, {'account': 'Bank', 'amount': 13000, 'rate': -130})
            with self.assertRaisesRegex(_Thrown, 'finite amount'):
                pi._build_payment(doc, {'account': 'Bank', 'amount': float('nan'), 'rate': 130})
            with self.assertRaisesRegex(_Thrown, 'more than the outstanding'):
                pi._build_payment(doc, {'account': 'Bank', 'amount': 13130, 'rate': 130})

class EditorWiring(unittest.TestCase):
    def test_D7_request_lines_use_real_stock_unit_and_factor(self):
        from vcl_procurement.api import raise_po, units
        doc = MutableDoc(name='MR', items=[Row(item_code='Paper', item_name='Paper', qty=2, ordered_qty=0, uom='Tonne', warehouse='RM', schedule_date='2026-11-09', name='MR-row')])
        with patch.object(frappe, 'get_doc', return_value=doc, create=True), patch.object(units, 'resolve_factor', return_value=('Kg', 1000)):
            result = raise_po.material_request_lines('MR')
        self.assertEqual((result['lines'][0]['stock_uom'], result['lines'][0]['conversion_factor']), ('Kg', 1000))

    def test_A6_receipt_save_keeps_original_rows_and_dates(self):
        from vcl_procurement.api import pr
        row = MutableDoc(name='row2', qty=2, rate=3, purchase_order_item='original-po-row', batch_no=None)
        doc = MutableDoc(name='PR', docstatus=0, items=[row])
        with patch.object(frappe, 'get_doc', return_value=doc, create=True), patch.object(pr, '_page', lambda d: d):
            result = pr.pr_save('PR', {'posting_date': '2026-10-05', 'supplier_delivery_note': ' DN ', 'lines': [{'name': 'row2', 'qty': 4, 'rate': 5}]})
            self.assertEqual((result.posting_date, result.set_posting_time, result.supplier_delivery_note), ('2026-10-05', 1, 'DN'))
            self.assertEqual((row.qty, row.rate, row.purchase_order_item), (4, 5, 'original-po-row'))
            self.assertTrue(doc.saved)
            with self.assertRaisesRegex(_Thrown, 'lines changed'):
                pr.pr_save('PR', {'posting_date': '2026-11-09', 'lines': [{'name': 'row1', 'qty': 1, 'rate': 1}]})

    def test_TRK_00023_PI_requires_every_actual_row_and_keeps_fixed_amounts(self):
        taxes = [Row(name='t1', charge_type='Actual', tax_amount=1), Row(name='t2', charge_type='Actual', tax_amount=2)]
        doc = MutableDoc(name='PI', items=[], taxes=taxes, currency='USD', conversion_rate=130)
        with self.assertRaisesRegex(_Thrown, 'Actual amount'):
            pi._lines_and_taxes(doc, {'taxes': {'mode': 'actual', 'rows': [{'name': 't1', 'kes': 130}]}})
        result = pi._lines_and_taxes(doc, {'taxes': {'mode': 'actual', 'rows': [{'name': 't1', 'kes': 130}, {'name': 't2', 'kes': 0}]}})
        self.assertEqual([r['tax_amount'] for r in result['taxes']], [1, 0])
        self.assertTrue(all(r['charge_type'] == 'Actual' for r in result['taxes']))

    def test_A8_item_VAT_uses_master_rates_and_preserves_links(self):
        import json
        original = Row(name='line', qty=2, rate=3, item_name='Original', item_tax_template=None, purchase_order='PO', po_detail='source-row')
        doc = MutableDoc(name='PI', company='VCL', items=[original], taxes=[])
        template = MutableDoc(company='VCL', disabled=0, taxes=[Row(tax_type='VAT - VCL', tax_rate=0)])
        with patch.object(frappe, 'get_doc', return_value=template, create=True), patch.object(frappe, 'as_json', json.dumps, create=True):
            result = pi._lines_and_taxes(doc, {'lines': [{'name': 'line', 'qty': 4, 'rate': 5, 'item_name': 'Supplier label', 'description': 'Paper <special>', 'item_tax_template': 'TAX EXEMPT - VCL'}]})
        line = result['items'][0]
        self.assertEqual((line['purchase_order'], line['po_detail'], line['item_name']), ('PO', 'source-row', 'Supplier label'))
        self.assertEqual(json.loads(line['item_tax_rate']), {'VAT - VCL': 0})
        self.assertIn('&lt;special&gt;', line['description'])

    def test_A11_amend_uses_no_copy_metadata_and_resets_QBO_links(self):
        from unittest.mock import Mock
        source = MutableDoc(name='PI', docstatus=2)
        copied = MutableDoc(name='PI-1', meta=Doc(has_field=lambda field: True))
        copy_doc = Mock(return_value=copied)
        with patch.object(frappe, 'get_doc', return_value=source, create=True), patch.object(frappe, 'get_list', return_value=[], create=True), patch.object(frappe, 'copy_doc', copy_doc, create=True):
            result = pi.pi_amend('PI')
        copy_doc.assert_called_once_with(source, ignore_no_copy=False)
        self.assertEqual((result['name'], copied.amended_from, copied.docstatus), ('PI-1', 'PI', 0))
        self.assertEqual(copied.custom_qbo_sync_status, 'Not Synced')
        self.assertIsNone(copied.custom_qbo_bill_id)


class ImportContext(unittest.TestCase):
    def test_A14_parent_context_and_custom_memo_keep_all_references(self):
        parent = MutableDoc(name='PARENT', supplier='Mill', supplier_name='Mill', bill_no='SUP-42',
            items=[Row(purchase_order='PO-42')], custom_container_details_table=[Row(container_reference='CONT-42')],
            custom_file_number='F-42', has_permission=lambda permission: True)
        child = MutableDoc(name='CHILD', custom_import_parent='PARENT', items=[], bill_no='Charge-42', currency='KES')
        with patch.object(frappe, 'get_doc', return_value=parent, create=True), patch.object(pi, '_from_po', return_value=None):
            context = pi._charge_context(child)
            self.assertEqual(context['name'], 'PARENT')
            self.assertEqual(context['purchase_orders'], ['PO-42'])
            memo = pi._memo_for(child, 'Own reference ERPNext CHILD')
            for reference in ('PARENT', 'PO-42', 'SUP-42', 'F-42', 'CONT-42'):
                self.assertIn(reference, memo)
            self.assertIsNone(pi._memo_for(child, memo))

class ExpenseInvoices(unittest.TestCase):
    def builder(self, payload):
        import sys, types
        from vcl_procurement.api import expense
        party = types.ModuleType('erpnext.accounts.party')
        party.get_party_details = lambda **kw: dict(currency='KES', credit_to='Creditors', tax_category='Domestic')
        controller = types.ModuleType('erpnext.controllers.accounts_controller')
        controller.get_taxes_and_charges = lambda *a: [dict(charge_type='On Net Total', rate=16, account_head='VAT')]
        doc = MutableDoc(name='DRAFT', items=[], taxes=[], flags=Doc())
        doc.append = lambda field, value: getattr(doc, field).append(Row(value))
        doc.set_missing_values = lambda: doc.taxes.append(Row(account_head='AUTO DEFAULT', tax_amount=999))
        doc.calculate_taxes_and_totals = lambda: None
        with patch.dict(sys.modules, {'erpnext.accounts.party': party, 'erpnext.controllers.accounts_controller': controller}), patch.object(expense, '_company', return_value='VCL'), patch.object(frappe, 'new_doc', return_value=doc, create=True), patch.object(frappe, 'get_doc', return_value=MutableDoc(company='VCL', is_group=0, disabled=0, root_type='Expense'), create=True), patch.object(frappe, 'get_cached_value', return_value='Main', create=True):
            return expense._build(payload)

    def test_A10_no_VAT_does_not_inherit_the_supplier_default_tax(self):
        doc = self.builder(dict(supplier='S', bill_no='X', bill_date='2026-11-09', vat=False,
            lines=[dict(description='Repairs', account='Repairs', qty=1, rate=1000)]))
        self.assertEqual(doc.taxes, [])
        self.assertIsNone(doc.taxes_and_charges)
        self.assertTrue(doc.flags.dont_auto_add_taxes)
        self.assertEqual((doc.posting_date, doc.bill_date, doc.update_stock), ('2026-11-09', '2026-11-09', 0))
        self.assertEqual(doc.items[0].expense_account, 'Repairs')

    def test_A10_fixed_VAT_is_a_complete_explicit_actual_row(self):
        doc = self.builder(dict(supplier='S', bill_no='X', bill_date='2026-11-09', vat=True, manual_vat=True, vat_amount=160,
            lines=[dict(description='Repairs', account='Repairs', qty=1, rate=1000)]))
        self.assertEqual(len(doc.taxes), 1)
        self.assertEqual((doc.taxes[0].charge_type, doc.taxes[0].tax_amount), ('Actual', 160))

    def test_A10_duplicate_draft_is_recovered_without_inserting_another_invoice(self):
        from vcl_procurement.api import expense
        doc = MutableDoc(supplier='S', bill_no='X')
        with patch.object(expense, '_build', return_value=doc), patch.object(frappe, 'get_list', return_value=[Row(name='EXISTING', docstatus=0)], create=True):
            self.assertEqual(expense.bill({}, save=1), {'name': 'EXISTING', 'existing': True})
            self.assertFalse(doc.get('inserted'))


class CalculatedTaxes(unittest.TestCase):
    def test_D13_restores_formulas_without_erasing_actual_template_charges(self):
        import sys, types
        controller = types.ModuleType('erpnext.controllers.accounts_controller')
        controller.get_taxes_and_charges = lambda *args: [dict(account_head='Insurance', charge_type='Actual', tax_amount=0), dict(account_head='VAT', charge_type='On Previous Row Total', row_id=1, rate=16)]
        doc = MutableDoc(name='PI', items=[], taxes_and_charges='Importation', taxes=[Row(name='t1', account_head='Insurance', charge_type='Actual', tax_amount=10), Row(name='t2', account_head='VAT', charge_type='Actual', tax_amount=20)])
        with patch.dict(sys.modules, {'erpnext.controllers.accounts_controller': controller}):
            result = pi._lines_and_taxes(doc, {'taxes': {'mode': 'calculated'}})
        self.assertEqual(result['taxes'][0]['tax_amount'], 10)
        self.assertEqual(result['taxes'][1]['charge_type'], 'On Previous Row Total')
        self.assertEqual(result['taxes'][1]['name'], 't2')
