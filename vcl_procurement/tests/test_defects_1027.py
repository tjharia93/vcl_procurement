"""The Purchasing defects and CRs triaged on 27/10/2026, with `frappe` stubbed.

    python3 vcl_procurement/tests/test_defects_1027.py
"""
import datetime
import sys
import types
import unittest


class _Thrown(Exception):
    pass


def _throw(msg, exc=None, title=None):
    raise _Thrown(msg)


class Row(dict):
    def __getattr__(self, k):
        return self.get(k)

    def __setattr__(self, k, v):
        self[k] = v

    def as_dict(self):
        return dict(self)


frappe = types.ModuleType("frappe")
frappe.whitelist = lambda *a, **k: (lambda f: f)
frappe.throw = _throw
frappe._ = lambda s: s
frappe.PermissionError = PermissionError
frappe.get_roles = lambda *a: ["Purchase Manager"]
frappe.parse_json = lambda v: v
frappe.session = types.SimpleNamespace(user="t@x")
frappe.db = types.SimpleNamespace(get_default=lambda k: "KES", get_value=lambda *a, **k: None,
                                  get_single_value=lambda *a: 0, exists=lambda *a, **k: True)
utils = types.ModuleType("frappe.utils")
utils.flt = lambda v, *a: float(v or 0)
utils.getdate = lambda v: v if isinstance(v, datetime.date) else datetime.date.fromisoformat(str(v)[:10])
utils.nowdate = lambda: "2026-10-27"
utils.today = utils.nowdate
utils.add_days = lambda d, n: d
utils.date_diff = lambda a, b: (utils.getdate(a) - utils.getdate(b)).days
frappe.utils = utils
sys.modules["frappe"] = frappe
sys.modules["frappe.utils"] = utils
erpnext_po = types.ModuleType("erpnext.buying.doctype.purchase_order.purchase_order")
for m in ("erpnext", "erpnext.buying", "erpnext.buying.doctype", "erpnext.buying.doctype.purchase_order"):
    sys.modules[m] = types.ModuleType(m)
sys.modules["erpnext.buying.doctype.purchase_order.purchase_order"] = erpnext_po

sys.path.insert(0, __file__.rsplit("/vcl_procurement/tests/", 1)[0])
from vcl_procurement.api import landing, pi, purchasing, rules  # noqa: E402
from vcl_procurement.patches.add_40hc_container_size import with_option  # noqa: E402


class Doc(types.SimpleNamespace):
    """A document: attributes, `get`, and a real `items` list (a dict's .items would shadow it)."""
    def get(self, k, d=None):
        return getattr(self, k, d)

    def check_permission(self, *a):
        pass

    def insert(self):
        self.name = "MAT-PRE-1"
        self.inserted = True


# --- item 1: pur-def-receipt-idx ---------------------------------------------------

class ReceiptRows(unittest.TestCase):
    PO = [{"name": "poi-1", "idx": 1}, {"name": "poi-2", "idx": 2}, {"name": "poi-3", "idx": 3}]

    def test_line_one_received_so_receipt_row_one_is_po_line_two(self):
        # The mapped receipt holds lines 2 and 3 only, numbered 1 and 2.
        pr = [{"purchase_order_item": "poi-2"}, {"purchase_order_item": "poi-3"}]
        self.assertEqual(rules.receipt_qty_by_row({2: 5.0}, self.PO, pr), [5.0, None])
        self.assertEqual(rules.receipt_qty_by_row({3: 2.5}, self.PO, pr), [None, 2.5])

    def test_zero_or_missing_drops_the_row(self):
        pr = [{"purchase_order_item": "poi-1"}, {"purchase_order_item": "poi-2"}]
        self.assertEqual(rules.receipt_qty_by_row({1: 0, 2: 4}, self.PO, pr), [None, 4.0])

    def test_a_row_from_another_order_is_dropped(self):
        self.assertEqual(rules.receipt_qty_by_row({1: 1}, self.PO, [{"purchase_order_item": "zz"}]), [None])


class CreateReceipt(unittest.TestCase):
    """create_receipt end to end: PO line 1 already in, Compass sends {"2": 3} (the PO idx)."""

    def setUp(self):
        self.po = Doc(name="PO-1", items=[
            Row(idx=1, name="poi-1", item_code="A", qty=10, received_qty=10),
            Row(idx=2, name="poi-2", item_code="B", qty=5, received_qty=0),
            Row(idx=3, name="poi-3", item_code="C", qty=4, received_qty=0)])
        self.pr = Doc(supplier="S", items=[
            Row(idx=1, purchase_order_item="poi-2", item_code="B", qty=5, uom="Kg"),
            Row(idx=2, purchase_order_item="poi-3", item_code="C", qty=4, uom="Kg")])
        frappe.get_doc = lambda dt, n=None: self.po
        frappe.get_all = lambda *a, **k: []
        frappe.db.get_value = lambda *a, **k: 0
        erpnext_po.make_purchase_receipt = lambda po: self.pr

    def test_partial_receipt_lands_on_the_po_line_asked_for(self):
        out = purchasing.create_receipt("PO-1", {"2": 3})
        self.assertEqual([(i["item_code"], i["qty"]) for i in out["items"]], [("B", 3.0)])

    def test_the_last_line_alone(self):
        out = purchasing.create_receipt("PO-1", {"3": 1.5})
        self.assertEqual([(i["item_code"], i["qty"]) for i in out["items"]], [("C", 1.5)])

    def test_a_received_line_is_refused(self):
        with self.assertRaises(_Thrown):
            purchasing.create_receipt("PO-1", {"1": 1})


# --- item 2: def-mvqeayxy, the amounts due follow the invoice ------------------------

class Refit(unittest.TestCase):
    def rows(self, a=(70, 700), b=(30, 300)):
        return [{"description": "Net", "invoice_portion": a[0], "payment_amount": a[1], "due_date": "2027-01-01"},
                {"description": "Taxes", "invoice_portion": b[0], "payment_amount": b[1], "due_date": "2026-11-01"}]

    def test_import_rows_follow_the_new_net_and_taxes(self):
        out = rules.refit_schedule(self.rows(), 900, 1200, True)
        self.assertEqual([r["payment_amount"] for r in out], [900, 300])
        self.assertAlmostEqual(out[0]["invoice_portion"], 75)
        self.assertEqual(out[1]["due_date"], "2026-11-01")

    def test_single_row_is_the_whole_invoice(self):
        out = rules.refit_schedule([{"invoice_portion": 100, "payment_amount": 1000}], 900, 1044, False)
        self.assertEqual(out, [{"invoice_portion": 100.0, "payment_amount": 1044}])

    def test_nothing_to_do_when_it_already_matches(self):
        self.assertIsNone(rules.refit_schedule(self.rows(), 700, 1000, True))

    def test_other_shapes_are_left_alone(self):
        self.assertIsNone(rules.refit_schedule(self.rows() + self.rows(), 700, 1000, True))
        self.assertIsNone(rules.refit_schedule([], 1, 1, False))


class SchedulePreviewFollowsTheEdit(unittest.TestCase):
    """The Due dates preview while editing: amounts from the lines as typed, not the last save."""

    def test_local_row_is_the_new_total(self):
        doc = Doc(name="PINV-2", docstatus=0, currency="KES", conversion_rate=1, net_total=1000, grand_total=1160,
                  rounded_total=0, bill_date="2026-10-01", custom_purchase_invoice_type="Local Purchase",
                  items=[Row(name="r1", idx=1, qty=10, rate=100), Row(name="r2", idx=2, qty=1, rate=0)], taxes=[])

        def update(data):
            for k, v in data.items():
                setattr(doc, k, v)

        def calc():
            doc.net_total = sum(float(r["qty"]) * float(r["rate"]) for r in doc.items)
            doc.grand_total = round(doc.net_total * 1.16, 2)
        doc.update, doc.calculate_taxes_and_totals = update, calc
        frappe.get_doc = lambda dt, n=None: doc
        saved, pi._term_row = pi._term_row, lambda t: ({"due_date_based_on": "Day(s) after invoice date", "credit_days": 30, "credit_months": 0}, "30 Days")
        self.addCleanup(setattr, pi, "_term_row", saved)
        out = pi.schedule_preview("PINV-2", {"terms_template": "30 Days", "lines": [{"name": "r1", "qty": 5, "rate": 100}], "remove_lines": ["r2"]})
        self.assertEqual([r["payment_amount"] for r in out["rows"]], [580.0])


# --- item 3: def-mvqebvoi, remove a PO line the invoice does not bill ------------------

class RemoveLines(unittest.TestCase):
    def doc(self):
        return Doc(name="PINV-1", currency="KES", conversion_rate=1, items=[
            Row(name="r1", idx=1, qty=1, rate=10), Row(name="r2", idx=2, qty=2, rate=20), Row(name="r3", idx=3, qty=3, rate=30)])

    def test_removed_rows_go_and_the_rest_are_renumbered(self):
        out = pi._lines_and_taxes(self.doc(), {"remove_lines": ["r2"], "lines": [{"name": "r3", "qty": 1, "rate": 30}]})
        self.assertEqual([(r["name"], r["idx"], r["qty"]) for r in out["items"]], [("r1", 1, 1), ("r3", 2, 1.0)])

    def test_the_last_line_cannot_go(self):
        with self.assertRaises(_Thrown):
            pi._lines_and_taxes(self.doc(), {"remove_lines": ["r1", "r2", "r3"]})

    def test_a_line_of_another_invoice_is_refused(self):
        with self.assertRaises(_Thrown):
            pi._lines_and_taxes(self.doc(), {"remove_lines": ["zz"]})

    def test_no_edit_leaves_the_items_out(self):
        self.assertEqual(pi._lines_and_taxes(self.doc(), {}), {})


# --- items 8 and 12: references --------------------------------------------------------

class References(unittest.TestCase):
    def test_file_no_order_of_trust(self):
        self.assertEqual(rules.file_no("MPPA 3334", "X", "S3399"), ("MPPA 3334", "the PO"))
        self.assertEqual(rules.file_no(None, " ", "S3399"), ("S3399", "the proforma (the PO has no file no.)"))
        self.assertEqual(rules.file_no(None, None, None), (None, None))

    def test_local_memo_carries_order_and_invoice(self):
        m = rules.qbo_memo("ACC-PINV-2026-00700", "INV-88", ["PUR-ORD-2026-03952"], None, None, None, [], "KES", 11600)
        self.assertEqual(m, "Supplier inv INV-88 · Order PUR-ORD-2026-03952 · ERPNext ACC-PINV-2026-00700 · KES 11,600.00 net")

    def test_import_memo_keeps_bl_and_containers(self):
        m = rules.qbo_memo("PINV-9", "CI-1", ["PO-9"], "S3399", "MPPA 3334", "MEDU123", ["MSKU1", "MSKU2"], "USD", 50000)
        self.assertTrue(m.startswith("BL MEDU123 · Containers MSKU1, MSKU2 · Supplier inv CI-1 · Order PO-9 (proforma S3399) · File no MPPA 3334"))

    def test_memo_is_net_with_the_rate_on_the_invoice_date(self):
        # cr-mvqewpg9: the amount is the net (no taxes), and a USD bill carries its rate and date.
        m = rules.qbo_memo("PINV-9", "CI-1", ["PO-9"], None, None, None, [], "USD", 103115.6, 130.05, "2026-09-14")
        self.assertTrue(m.endswith("ERPNext PINV-9 · USD 103,115.60 net · rate 130.05 on 14/09/2026"), m)
        self.assertTrue(rules.qbo_memo("P", None, [], currency="USD", total=10, rate=129.5).endswith("USD 10.00 net · rate 129.5"))
        self.assertNotIn("rate", rules.qbo_memo("P", None, [], currency="KES", total=10, rate=1))

    def test_typed_memo_kept_only_when_it_has_the_refs(self):
        self.assertTrue(rules.memo_has_refs("paper for PO-9 ref PINV-9", "PINV-9", ["PO-9"]))
        self.assertFalse(rules.memo_has_refs("paper", "PINV-9", ["PO-9"]))
        self.assertFalse(rules.memo_has_refs("", "PINV-9", []))

    def test_memo_for_appends_missing_refs_to_a_typed_memo(self):
        frappe.db.get_value = lambda *a, **k: None
        doc = Doc(name="PINV-9", bill_no="CI-1", currency="KES", grand_total=10,
                  items=[Row(purchase_order="PO-9")], custom_container_details_table=[])
        self.assertEqual(pi._memo_for(doc, "Kraft liner"), "Kraft liner · Order PO-9 · ERPNext PINV-9")
        self.assertIsNone(pi._memo_for(doc, "Kraft liner · Order PO-9 · ERPNext PINV-9"))
        self.assertTrue(pi._memo_for(doc, "").startswith("Supplier inv CI-1 · Order PO-9 · ERPNext PINV-9"))


# --- items 7-9: the shipment record ------------------------------------------------------

class Shipment(unittest.TestCase):
    SHIPS = [{"name": "SHIP-1", "bl_number": "MEDU 123", "purchase_order": "PO-1"},
             {"name": "SHIP-2", "bl_number": None, "purchase_order": "PO-1"},
             {"name": "SHIP-3", "bl_number": None, "purchase_order": "PO-2"}]

    def test_bl_match_wins_spaces_and_case_ignored(self):
        self.assertEqual(rules.pick_shipment(self.SHIPS, "medu-123", ["PO-2"])["name"], "SHIP-1")

    def test_single_shipment_of_the_order(self):
        self.assertEqual(rules.pick_shipment(self.SHIPS, None, ["PO-2"])["name"], "SHIP-3")

    def test_split_order_without_bl_is_not_guessed(self):
        self.assertIsNone(rules.pick_shipment(self.SHIPS, None, ["PO-1"]))

    def test_40hc_option_added_once(self):
        self.assertEqual(with_option("20 FT\n40 FT"), "20 FT\n40 FT\n40 HC")
        self.assertEqual(with_option("\n20 FT\n40 FT\n"), "\n20 FT\n40 FT\n40 HC")
        self.assertIsNone(with_option("20 FT\n40 HC"))
        self.assertEqual(with_option(""), "40 HC")


# --- item 5: def-pc1027-03, a BL found before its invoice exists ----------------------------

class BlFinder(unittest.TestCase):
    def test_shipment_found_by_bl_with_no_invoice(self):
        tables = {"Import Shipment": [Row(name="SHIP-7", bl_number="MEDU9988776", purchase_order="PO-7",
                                          supplier="Mill", stage="3 Draft BL reviewed", proforma_no="S1")]}
        frappe.get_all = lambda dt, **k: list(tables.get(dt, []))
        frappe.db.exists = lambda *a, **k: True
        out = landing.container_search("medu 99887")
        self.assertEqual([(h["kind"], h["shipment"], h["invoice"], h["pos"]) for h in out],
                         [("shipment", "SHIP-7", None, ["PO-7"])])


if __name__ == "__main__":
    unittest.main(verbosity=1)
