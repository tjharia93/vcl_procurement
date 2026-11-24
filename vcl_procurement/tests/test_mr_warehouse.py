"""Material Request creation on ERPNext with and without the legacy stock field."""
import datetime
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


class RequestWarehouse(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType('frappe')
        f = self.frappe
        f.whitelist = lambda **kw: lambda fn: fn
        f._ = lambda value: value
        f.throw = Mock(side_effect=ValueError)
        f.defaults = types.SimpleNamespace(get_user_default=lambda key: 'VCL' if key == 'Company' else None)
        f.db = types.SimpleNamespace(get_single_value=Mock(side_effect=AssertionError('Missing singleton field queried')), get_value=Mock(return_value='Item warehouse - VCL'), commit=Mock())
        f.get_meta = Mock(return_value=types.SimpleNamespace(has_field=lambda field: False))
        self.doc = types.SimpleNamespace(items=[], append=lambda field, row: self.doc.items.append(row), insert=Mock(), name='QA-MR')
        f.new_doc = lambda dt: self.doc
        utils = types.ModuleType('frappe.utils')
        utils.flt = lambda value: float(value or 0)
        utils.getdate = lambda value: datetime.date.fromisoformat(value)
        utils.today = lambda: '2026-11-24'
        purchasing = types.ModuleType('vcl_procurement.api.purchasing')
        purchasing._assert_purchasing_role = lambda: None
        path = Path(__file__).parents[1] / 'api' / 'mr.py'
        spec = importlib.util.spec_from_file_location('mr_warehouse_contract', path)
        self.mr = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'frappe': f, 'frappe.utils': utils, 'vcl_procurement.api.purchasing': purchasing}):
            spec.loader.exec_module(self.mr)
        self.mr.request = lambda name: {'name': name}

    def create(self, **line):
        return self.mr.create_request([dict(item_code='QA', qty=1, **line)], schedule_date='2026-11-26')

    def test_removed_stock_field_does_not_break_an_item_default(self):
        self.assertEqual(self.create(), {'name': 'QA-MR'})
        self.assertEqual(self.doc.items[0]['warehouse'], 'Item warehouse - VCL')
        self.frappe.db.get_single_value.assert_not_called()
        self.doc.insert.assert_called_once()

    def test_explicit_warehouse_works_for_new_item_without_default(self):
        self.frappe.db.get_value.return_value = None
        self.create(warehouse='Chosen warehouse - VCL')
        self.assertEqual(self.doc.items[0]['warehouse'], 'Chosen warehouse - VCL')

    def test_missing_warehouse_refuses_before_inserting(self):
        self.frappe.db.get_value.return_value = None
        with self.assertRaises(ValueError):
            self.create()
        self.doc.insert.assert_not_called()
        self.frappe.db.commit.assert_not_called()

    def test_existing_legacy_default_is_supported(self):
        self.frappe.get_meta.return_value.has_field = lambda field: True
        self.frappe.db.get_value.return_value = None
        self.frappe.db.get_single_value.side_effect = None
        self.frappe.db.get_single_value.return_value = 'Legacy - VCL'
        self.create()
        self.assertEqual(self.doc.items[0]['warehouse'], 'Legacy - VCL')

    def test_item_default_precedes_user_default(self):
        self.frappe.defaults.get_user_default = lambda key: 'VCL' if key == 'Company' else 'User - VCL'
        self.create()
        self.assertEqual(self.doc.items[0]['warehouse'], 'Item warehouse - VCL')


if __name__ == '__main__':
    unittest.main()
