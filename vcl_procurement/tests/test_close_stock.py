"""Close stock at submit (02/11/2026): the pure plan, and pi_submit(close_stock=1) with `frappe` stubbed.

    python3 -m unittest vcl_procurement.tests.test_close_stock
"""
import types
import unittest

from vcl_procurement.tests import test_cr_1031 as base
from vcl_procurement.api import rules


def line(**k):
    d = {"item_code": "PAPER", "item_name": "Paper", "qty": 5, "uom": "Kg", "warehouse": "Stores - VCL", "cost_center": "Main - VCL",
         "expense_account": "5000 - Cost of Goods Sold - VCL", "is_stock_item": 1, "has_batch_no": 0, "has_serial_no": 0}
    d.update(k)
    return d


class IssuePlan(unittest.TestCase):
    def test_stock_line_becomes_a_row(self):
        rows, problems = rules.issue_plan([line()])
        self.assertEqual(problems, [])
        self.assertEqual(rows, [{"item_code": "PAPER", "qty": 5.0, "uom": "Kg", "s_warehouse": "Stores - VCL",
                                 "expense_account": "5000 - Cost of Goods Sold - VCL", "cost_center": "Main - VCL"}])

    def test_service_lines_are_skipped(self):
        rows, problems = rules.issue_plan([line(), line(item_code="FREIGHT", is_stock_item=0)])
        self.assertEqual([r["item_code"] for r in rows], ["PAPER"])
        self.assertEqual(problems, [])

    def test_no_stock_items_is_refused(self):
        rows, problems = rules.issue_plan([line(is_stock_item=0)])
        self.assertEqual(rows, [])
        self.assertTrue(problems)

    def test_batch_and_serial_are_refused(self):
        for k in ("has_batch_no", "has_serial_no"):
            _, problems = rules.issue_plan([line(**{k: 1})])
            self.assertEqual(len(problems), 1, k)

    def test_missing_warehouse_account_cost_centre_are_each_named(self):
        _, problems = rules.issue_plan([line(warehouse=None, expense_account=None, cost_center=None)])
        self.assertEqual(len(problems), 3)


class Item(types.SimpleNamespace):
    def get(self, k, d=None):
        return getattr(self, k, d)


class PiSubmitClose(unittest.TestCase):
    def _run(self, plan_problems=(), close=1, is_return=0):
        pi = base._stub_frappe()
        f = pi.frappe
        doc = base.Doc(name="ACC-PINV-X", docstatus=0, update_stock=0, company="VCL", posting_date="2026-11-02", is_return=is_return,
                       items=[Item(item_code="PAPER", item_name="Paper", qty=5, uom="Kg", warehouse="Stores - VCL", cost_center="Main - VCL")],
                       comments=[], saved=0, submitted=0)
        doc.save = lambda: setattr(doc, "saved", doc.saved + 1)
        def submit():
            doc.submitted += 1
            doc.docstatus = 1
        doc.submit = submit
        made = []
        class SE(base.Doc):
            def insert(s): made.append(("insert", s.items)); return s
            def submit(s): made.append(("submit", None))
        saved = {k: getattr(f, k, None) for k in ("get_doc", "throw", "get_roles", "_", "session")}
        saved_pi = {k: getattr(pi, k) for k in ("pi_page", "_missing", "_receipt_refusal", "gate_before_submit", "_assert_purchasing_role", "_issue_lines", "_")}
        f.throw = base._throw
        f._ = lambda s: s
        f.get_doc = lambda a, b=None: SE(name="MAT-STE-1", **a) if isinstance(a, dict) else doc
        pi.pi_page = lambda n: "page"
        pi._ = lambda s: s
        pi._missing = lambda d: []
        pi._receipt_refusal = lambda d: ""
        pi.gate_before_submit = lambda d: None
        pi._assert_purchasing_role = lambda: None
        pi._issue_lines = lambda d: [line()] if not plan_problems else [line(has_batch_no=1)]
        try:
            try:
                out = pi.pi_submit("ACC-PINV-X", close)
            except base._Thrown as e:
                out = e
            return out, doc, made
        finally:
            for k, v in saved.items():
                setattr(f, k, v)
            for k, v in saved_pi.items():
                setattr(pi, k, v)

    def test_tick_receives_through_the_invoice_and_issues_out(self):
        out, doc, made = self._run()
        self.assertEqual(out, "page")
        self.assertEqual((doc.update_stock, doc.saved, doc.submitted), (1, 1, 1))
        self.assertEqual([m[0] for m in made], ["insert", "submit"])
        self.assertEqual(made[0][1][0]["qty"], 5.0)
        self.assertIn("MAT-STE-1", doc.comments[0])

    def test_unticked_is_the_old_behaviour(self):
        out, doc, made = self._run(close=0)
        self.assertEqual((out, doc.update_stock, doc.submitted, made), ("page", 0, 1, []))

    def test_a_problem_refuses_before_anything_is_saved_or_submitted(self):
        out, doc, made = self._run(plan_problems=("x",))
        self.assertIsInstance(out, base._Thrown)
        self.assertEqual((doc.update_stock, doc.saved, doc.submitted, made), (0, 0, 0, []))

    def test_debit_note_is_refused(self):
        out, doc, made = self._run(is_return=1)
        self.assertIsInstance(out, base._Thrown)
        self.assertEqual((doc.saved, doc.submitted, made), (0, 0, []))


if __name__ == "__main__":
    unittest.main()
