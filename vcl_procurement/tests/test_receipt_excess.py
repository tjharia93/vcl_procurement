import copy
import types
import unittest
from unittest.mock import patch
from vcl_procurement.tests.test_reel_intake import frappe, Doc, Row, _Thrown
from vcl_procurement.api import receipt_excess as api, receipt_excess_rules as rules


class ExcessRules(unittest.TestCase):
    def setUp(self):
        self.rows=[dict(name='a',idx=1,item_code='1525',item_name='1525 mm / 125 GSM',qty=3500,received_qty=0,rate=83,uom='Kg',conversion_factor=1,description='<p>original A</p>',schedule_date='2026-11-13'),
                   dict(name='b',idx=2,item_code='1200',item_name='1200 mm / 150 GSM',qty=2000,received_qty=0,rate=83,uom='Kg',conversion_factor=1,description='<p>original B</p>',schedule_date='2026-11-13')]
        self.limits={1:(10,3850),2:(10,2200)}

    def test_actual_delivery_proposes_only_the_excess_line_at_its_existing_rate(self):
        original=copy.deepcopy(self.rows)
        wanted,changes=rules.proposal(self.rows,{'1':3325,'2':3569},self.limits)
        self.assertEqual(wanted,{'1':3325,'2':3569})
        self.assertEqual(len(changes),1)
        self.assertEqual((changes[0]['new_order_qty'],changes[0]['extra_qty'],changes[0]['extra_net']),(3569,1569,130227))
        items=rules.native_items(self.rows,changes)
        self.assertEqual([r['docname'] for r in items],['a','b'])
        self.assertEqual([r['qty'] for r in items],[3500,3569])
        self.assertEqual([r['rate'] for r in items],[83,83])
        self.assertEqual([r['description'] for r in items],[r['description'] for r in self.rows])
        self.assertEqual(self.rows,original)

    def test_receipts_within_allowance_need_no_po_quantity_change(self):
        self.assertEqual(rules.proposal(self.rows,{'2':2200},self.limits)[1],[])

    def test_partial_receipts_are_added_to_the_new_order_total(self):
        self.rows[1]['received_qty']=500
        _,changes=rules.proposal(self.rows,{'2':2000},{1:(10,3850),2:(10,1650)})
        self.assertEqual((changes[0]['new_order_qty'],changes[0]['extra_qty']),(2500,500))

    def test_invalid_or_cross_order_values_are_refused(self):
        for wanted in ({'3':1},{'2':float('nan')},{'2':-1},{'2':True},{'2':0},{},[]):
            with self.assertRaises(ValueError):rules.proposal(self.rows,wanted,self.limits)


class Approval(ExcessRules):
    def setUp(self):
        super().setUp()
        self.comments=[];self.native_calls=[]
        self.doc=Doc(name='PO',docstatus=1,status='To Receive and Bill',modified='v1',currency='KES',supplier='Chentai',grand_total=529540,items=[Row(r) for r in self.rows])
        self.doc.check_permission=lambda *_:None
        self.doc.has_permission=lambda *_:True
        def add_comment(kind,text):
            comment=Row(name='COMMENT-'+str(len(self.comments)+1),content=text)
            self.comments.append(comment);return comment
        self.doc.add_comment=add_comment
        def native(po,items):
            self.native_calls.append((po,items))
            for row in self.doc.items:row.qty=next(r['qty'] for r in items if r['docname']==row.name)
            self.doc.modified='v2'
        self.stack=[patch.object(api,'_assert_purchasing_role'),patch.object(frappe,'get_roles',return_value=['Purchase Manager']),
            patch.object(frappe,'get_doc',return_value=self.doc,create=True),
            patch.object(frappe,'get_all',side_effect=lambda *a,**k:self.comments,create=True),
            patch.object(frappe,'db',types.SimpleNamespace(sql=lambda *a,**k:None),create=True),
            patch.object(api,'_receipt_limits',side_effect=lambda doc:(10,{r.idx:(10,(r.qty-r.received_qty)*1.1) for r in doc.items})),
            patch.object(api,'receipt_preview',return_value={'po':'PO','items':[]}),patch.object(api,'_native_update',side_effect=native),
            patch.object(api,'nowdate',return_value='2026-11-16')]
        for p in self.stack:p.start();self.addCleanup(p.stop)
        self.args=dict(po='PO',lines={'1':3325,'2':3569},expected_modified='v1',reason='Actual delivery <checked>',approval_id='approval-0001',posting_date='2026-11-16',supplier_delivery_note='0005972')

    def test_preview_is_read_only_and_has_the_exact_additional_value(self):
        p=api.preview('PO',self.args['lines'])
        self.assertEqual(p['additional_net'],130227)
        self.assertTrue(p['can_approve'])
        self.assertEqual((self.native_calls,self.comments),([],[]))

    def test_manager_approval_is_a_native_po_update_plus_an_escaped_audit_note(self):
        result=api.approve(**self.args)
        self.assertTrue(result['approved']);self.assertFalse(result['stock_posted'])
        self.assertEqual([r.qty for r in self.doc.items],[3500,3569])
        self.assertIn('0005972',self.comments[0].content)
        self.assertIn('&lt;checked&gt;',self.comments[0].content)
        self.assertEqual(len(self.native_calls),1)

    def test_retry_reuses_the_approval_without_updating_again(self):
        api.approve(**self.args)
        result=api.approve(**self.args)
        self.assertTrue(result['reused'])
        self.assertEqual((len(self.native_calls),len(self.comments)),(1,1))
        with self.assertRaises(_Thrown):api.approve(**{**self.args,'reason':'Changed'})

    def test_stale_or_unprivileged_approval_cannot_update_the_order(self):
        with self.assertRaisesRegex(_Thrown,'changed'):api.approve(**{**self.args,'expected_modified':'old'})
        with patch.object(frappe,'get_roles',return_value=['Purchase User']):
            with self.assertRaises(_Thrown):api.approve(**self.args)
        self.assertEqual((self.native_calls,self.comments),([],[]))

    def test_native_workflow_refusal_does_not_create_an_approval_note(self):
        with patch.object(api,'_native_update',side_effect=PermissionError('Workflow refused')):
            with self.assertRaises(PermissionError):api.approve(**self.args)
        self.assertEqual(self.comments,[])

    def test_future_date_and_empty_reason_stop_before_mutation(self):
        for extra in ({'posting_date':'2026-11-17'},{'reason':''},{'approval_id':'bad%id'}):
            with self.assertRaises(_Thrown):api.approve(**{**self.args,**extra})
        self.assertEqual(self.native_calls,[])
