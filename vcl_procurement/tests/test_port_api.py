"""The server side of the Compass purchasing port, with `frappe` stubbed: list rows, landing search,
PO/PI pages and saves, billing from a PO. What is asserted is the shape the React screens read and
the rule each endpoint applies; real documents are checked after deploy.

    python3 vcl_procurement/tests/test_port_api.py
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
frappe.get_roles = lambda: ["Purchase Manager"]
frappe.parse_json = lambda v: v
frappe.session = types.SimpleNamespace(user="t@x")
frappe.db = types.SimpleNamespace(get_default=lambda k: "KES", get_value=lambda *a, **k: None)
utils = types.ModuleType("frappe.utils")
utils.flt = lambda v: float(v or 0)
utils.getdate = lambda v: v if isinstance(v, datetime.date) else datetime.date.fromisoformat(str(v)[:10])
utils.nowdate = lambda: "2026-10-01"
utils.today = utils.nowdate
utils.add_days = lambda d, n: d
utils.date_diff = lambda a, b: (utils.getdate(a) - utils.getdate(b)).days
frappe.utils = utils
sys.modules["frappe"] = frappe
sys.modules["frappe.utils"] = utils

sys.path.insert(0, __file__.rsplit("/vcl_procurement/tests/", 1)[0])
from vcl_procurement.api import landing, pi, po, purchasing, raise_po  # noqa: E402


class FakeDB:
    """frappe.get_all over canned tables, recording each call."""

    def __init__(self, tables):
        self.tables = tables
        self.calls = []

    def get_all(self, doctype, filters=None, fields=None, **kw):
        self.calls.append((doctype, filters))
        rows = [Row(r) for r in self.tables.get(doctype, [])]
        for k, v in (filters or {}).items():
            if isinstance(v, list) and v and v[0] == "in":
                rows = [r for r in rows if r.get(k) in v[1]]
            elif isinstance(v, list) and v and v[0] == "<":
                rows = [r for r in rows if (r.get(k) or 0) < v[1]]
            elif isinstance(v, list) and v and v[0] == "is":
                rows = [r for r in rows if r.get(k)]
            elif not isinstance(v, list):
                rows = [r for r in rows if r.get(k) == v]
        return rows


def install(tables):
    db = FakeDB(tables)
    frappe.get_all = db.get_all
    return db


# --- purchasing lists ------------------------------------------------------------

def po_row(name, due, **kw):
    base = {"name": name, "supplier": "S", "transaction_date": "2026-01-01", "schedule_date": due,
            "status": "To Receive and Bill", "base_grand_total": 100, "grand_total": 100, "per_received": 0,
            "per_billed": 0, "currency": "KES", "custom_order_type": "Import", "order_confirmation_no": "PFI-1",
            "custom_file_number": "F1", "docstatus": 1}
    base.update(kw)
    return base


class OrderLists(unittest.TestCase):
    def setUp(self):
        self.db = install({
            "Purchase Order": [po_row("PO-1", "2026-05-01"), po_row("PO-2", "2026-09-20"),
                               po_row("PO-3", "2026-12-01", custom_order_type=None),
                               po_row("PO-D", "2026-05-01", docstatus=0, workflow_state="Draft", owner="a")],
            "Purchase Order Item": [
                {"parent": "PO-1", "idx": 1, "item_name": "PAPER", "description": "<p>Woodfree  80gsm</p>"},
                {"parent": "PO-1", "idx": 2, "item_name": "INK", "description": ""},
                {"parent": "PO-1", "idx": 3, "item_name": "GLUE", "description": None},
                {"parent": "PO-2", "idx": 1, "item_name": "BOARD", "description": "<p>BOARD</p>"},
                {"parent": "PO-D", "idx": 1, "item_name": "FILM", "description": None}],
        })

    def rows(self):
        return {r["name"]: r for r in purchasing.open_orders()["rows"]}

    def test_stale_is_overdue_and_more_than_90_days(self):
        r = self.rows()
        self.assertTrue(r["PO-1"]["overdue"] and r["PO-1"]["stale"])
        self.assertTrue(r["PO-2"]["overdue"] and not r["PO-2"]["stale"])
        self.assertFalse(r["PO-3"]["overdue"] or r["PO-3"]["stale"])

    def test_fully_received_is_never_stale(self):
        self.db.tables["Purchase Order"][0]["per_received"] = 100
        self.assertFalse(self.rows()["PO-1"]["stale"])

    def test_ordered_first_line_and_more(self):
        r = self.rows()
        self.assertEqual(r["PO-1"]["ordered"], {"text": "Woodfree 80gsm", "more": 2})
        self.assertEqual(r["PO-2"]["ordered"], {"text": "BOARD", "more": 0})

    def test_order_without_lines_has_empty_summary(self):
        self.assertEqual(self.rows()["PO-3"]["ordered"], {"text": "", "more": 0})

    def test_lines_come_from_one_query_for_all_orders(self):
        self.rows()
        self.assertEqual(sum(1 for c in self.db.calls if c[0] == "Purchase Order Item"), 1)
        purchasing.draft_orders()
        self.assertEqual(sum(1 for c in self.db.calls if c[0] == "Purchase Order Item"), 2)

    def test_open_rows_carry_type_confirmation_and_file(self):
        r = self.rows()
        self.assertEqual(r["PO-1"]["order_type"], "Import")
        self.assertEqual(r["PO-3"]["order_type"], "Local")
        self.assertEqual(r["PO-1"]["order_confirmation_no"], "PFI-1")
        self.assertEqual(r["PO-1"]["confirmation_no"], "PFI-1")      # the existing key stays
        self.assertEqual(r["PO-1"]["file_no"], "F1")

    def test_draft_rows(self):
        d = purchasing.draft_orders()
        self.assertEqual(len(d), 1)
        d = d[0]
        self.assertFalse(d["stale"])
        self.assertEqual(d["ordered"], {"text": "FILM", "more": 0})
        self.assertEqual((d["order_type"], d["order_confirmation_no"], d["file_no"]), ("Import", "PFI-1", "F1"))
        for k in ("name", "supplier", "date", "due", "currency", "total", "workflow_state", "raised_by"):
            self.assertIn(k, d)


# --- landing ---------------------------------------------------------------------

class LandingSearch(unittest.TestCase):
    def setUp(self):
        install({
            "Container Shipping Details": [
                {"parent": "PI-1", "parenttype": "Purchase Invoice", "container_reference": "MSKU 9594207", "container_size": "40"},
                {"parent": "PI-X", "parenttype": "Purchase Invoice", "container_reference": "MSKU 9594208", "container_size": "40"}],
            "Purchase Invoice": [
                {"name": "PI-1", "supplier": "A", "supplier_name": "Alpha", "bill_no": "B1", "docstatus": 1,
                 "custom_bill_of_lading_number": "MEDU 123456"},
                {"name": "PI-2", "supplier": "B", "supplier_name": "Beta", "bill_no": "B2", "docstatus": 0,
                 "custom_bill_of_lading_number": "MEDU-123456-X"},
                {"name": "PI-X", "supplier": "C", "supplier_name": "Cancelled", "bill_no": "B3", "docstatus": 2,
                 "custom_bill_of_lading_number": "MEDU123456"}],
            "Purchase Invoice Item": [{"parent": "PI-2", "purchase_order": "PO-9"}],
        })

    def test_bl_hits_and_container_hits_are_tagged(self):
        out = landing.container_search("medu123456")
        self.assertEqual([(h["kind"], h["invoice"]) for h in out], [("bl", "PI-1"), ("bl", "PI-2")])
        pi2 = out[1]
        self.assertEqual((pi2["supplier"], pi2["bill_no"], pi2["pos"], pi2["draft"], pi2["bl_no"]),
                         ("Beta", "B2", ["PO-9"], True, "MEDU-123456-X"))
        self.assertIsNone(pi2["container"])
        self.assertIsNone(pi2["size"])

    def test_container_hit_keeps_its_keys_and_gets_kind(self):
        out = landing.container_search("9594207")
        self.assertEqual(len(out), 1)
        h = out[0]
        self.assertEqual((h["kind"], h["container"], h["size"], h["invoice"], h["bl_no"]),
                         ("container", "MSKU 9594207", "40", "PI-1", "MEDU 123456"))

    def test_cancelled_invoices_are_excluded(self):
        self.assertNotIn("PI-X", [h["invoice"] for h in landing.container_search("9594208")])
        self.assertNotIn("PI-X", [h["invoice"] for h in landing.container_search("MEDU")])

    def test_short_query_matches_nothing(self):
        self.assertEqual(landing.container_search("me"), [])

    def test_pi_list_carries_bl_no(self):
        install({"Purchase Invoice": [
            {"name": "PI-1", "docstatus": 1, "is_return": 0, "custom_bill_of_lading_number": " MEDU 1 "},
            {"name": "PI-2", "docstatus": 1, "is_return": 0, "custom_bill_of_lading_number": None}]})
        rows = {r["name"]: r for r in landing.pi_list()}
        self.assertEqual(rows["PI-1"]["bl_no"], "MEDU 1")
        self.assertIsNone(rows["PI-2"]["bl_no"])


# --- PO page / save ----------------------------------------------------------------

class Doc:
    """A document: attribute and item access over one namespace (a dict subclass would shadow `.items`)."""

    def __init__(self, **kw):
        self.__dict__.update(kw)
        self.saved = False

    def get(self, k, d=None):
        return self.__dict__.get(k, d)

    def __getitem__(self, k):
        return self.__dict__[k]

    def __setitem__(self, k, v):
        self.__dict__[k] = v

    def __contains__(self, k):
        return k in self.__dict__

    def pop(self, k):
        return self.__dict__.pop(k)

    def update(self, d):
        # Like frappe, child rows come back as documents, not bare dicts.
        for k, v in d.items():
            self.__dict__[k] = [Row(r) if isinstance(r, dict) else r for r in v] if isinstance(v, list) else v

    def save(self):
        self.saved = True

    def check_permission(self, *_a):
        pass

    def calculate_taxes_and_totals(self):
        pass


def po_doc(**kw):
    items = [Row(name="r1", idx=1, item_code="A", item_name="A", qty=1, uom="Nos", stock_uom="Nos", conversion_factor=1,
                 rate=5, amount=5, warehouse="W", schedule_date="2026-10-10", received_qty=0, billed_amt=0,
                 material_request=None, description='<div class="ql-editor read-mode"><p>Keep <b>me</b></p></div>')]
    base = dict(name="PO-1", docstatus=0, status="Draft", workflow_state="Draft", supplier="S", supplier_name="S",
                currency="KES", conversion_rate=1, custom_order_type="Local", transaction_date="2026-10-01",
                schedule_date="2026-10-10", net_total=5, base_net_total=5, grand_total=5, base_grand_total=5,
                per_received=0, per_billed=0, items=items, taxes=[], payment_schedule=[],
                custom_comments__="Call first")
    base.update(kw)
    return Doc(**base)


class PoPageAndSave(unittest.TestCase):
    def setUp(self):
        self.doc = po_doc()
        frappe.get_doc = lambda dt, name=None: self.doc if dt == "Purchase Order" else None
        frappe.db.get_value = lambda dt, name, field=None, **k: "Nos" if dt == "Item" else None
        install({})
        self._ao = po._connections, po._workflow_actions, po._payment_terms, po._tax_templates
        po._connections = lambda n: {"receipts": [], "invoices": []}
        po._workflow_actions = lambda d: []
        po._payment_terms = lambda: []
        po._tax_templates = lambda: []

    def tearDown(self):
        po._connections, po._workflow_actions, po._payment_terms, po._tax_templates = self._ao

    def line(self, **kw):
        ln = {"name": "r1", "item_code": "A", "qty": 1, "uom": "Nos", "conversion_factor": 1, "rate": 5}
        ln.update(kw)
        return ln

    def saved_row(self):
        return self.doc["items"][0]

    def test_page_has_note_and_plain_line_description(self):
        p = po.po_page("PO-1")
        self.assertEqual(p["note"], "Call first")
        self.assertEqual(p["lines"][0]["description"], "Keep me")
        self.doc["custom_comments__"] = None
        self.assertEqual(po.po_page("PO-1")["note"], "")

    def test_untouched_description_keeps_original_html(self):
        po.po_save("PO-1", {"lines": [self.line(description="Keep me")]})
        self.assertIn("<b>me</b>", self.saved_row()["description"])

    def test_changed_description_is_written_as_editor_html(self):
        po.po_save("PO-1", {"lines": [self.line(description="New text")]})
        self.assertEqual(self.saved_row()["description"], '<div class="ql-editor read-mode"><p>New text</p></div>')

    def test_new_line_with_description_gets_it(self):
        po.po_save("PO-1", {"lines": [self.line(), self.line(name=None, description="Fresh")]})
        self.assertEqual(self.doc["items"][1]["description"], '<div class="ql-editor read-mode"><p>Fresh</p></div>')

    def test_line_without_description_key_is_untouched(self):
        po.po_save("PO-1", {"lines": [self.line()]})
        self.assertIn("<b>me</b>", self.saved_row()["description"])

    def test_note_and_order_date_saved(self):
        po.po_save("PO-1", {"note": "  ", "order_date": "2026-10-05"})
        self.assertIsNone(self.doc["custom_comments__"])
        self.assertEqual(self.doc["transaction_date"], "2026-10-05")
        po.po_save("PO-1", {"note": "Deliver to dock 2"})
        self.assertEqual(self.doc["custom_comments__"], "Deliver to dock 2")

    def test_order_date_after_required_by_is_refused(self):
        with self.assertRaises(_Thrown) as cm:
            po.po_save("PO-1", {"order_date": "2026-10-11"})
        self.assertIn("order date", str(cm.exception))
        with self.assertRaises(_Thrown):
            po.po_save("PO-1", {"order_date": "2026-10-08", "required_by": "2026-10-07"})
        self.assertFalse(self.doc.saved)

    def test_price_is_still_only_rate(self):
        po.po_save("PO-1", {"lines": [self.line(rate=9, margin_type="Amount", price_list_rate=1)]})
        r = self.saved_row()
        self.assertEqual(r["rate"], 9.0)
        self.assertNotIn("margin_type", r)


# --- raise PO ----------------------------------------------------------------------

class RaiseExtras(unittest.TestCase):
    def setUp(self):
        self.doc = Doc(items=[], name="PUR-ORD-T", docstatus=0, status="Draft", currency="KES", grand_total=0)
        self.doc.append = lambda f, v: self.doc["items"].append(Row()) or self.doc["items"][-1]
        self.doc.insert = lambda: None
        frappe.new_doc = lambda dt: self.doc

    def call(self, **kw):
        kw.setdefault("supplier", "S")
        kw.setdefault("lines", [{"item_code": "A", "qty": 1, "rate": 2}])
        return raise_po.create_purchase_order(**kw)

    def test_order_date_note_and_description(self):
        self.call(schedule_date="2026-10-20", order_date="2026-10-05", note=" hello ",
                  lines=[{"item_code": "A", "qty": 1, "rate": 2, "description": "Two\nlines"},
                         {"item_code": "B", "qty": 1, "rate": 2, "description": "  "}])
        self.assertEqual(self.doc.transaction_date, datetime.date(2026, 10, 5))
        self.assertEqual(self.doc.custom_comments__, "hello")
        self.assertEqual(self.doc["items"][0].description,
                         '<div class="ql-editor read-mode"><p>Two</p><p>lines</p></div>')
        self.assertNotIn("description", self.doc["items"][1])

    def test_order_date_after_schedule_is_refused(self):
        with self.assertRaises(_Thrown):
            self.call(schedule_date="2026-10-05", order_date="2026-10-06")

    def test_defaults_unchanged_without_the_new_args(self):
        self.call()
        self.assertEqual(self.doc.transaction_date, "2026-10-01")
        self.assertNotIn("custom_comments__", self.doc)


# --- PI --------------------------------------------------------------------------

POS = {"PO-IMP": {"custom_order_type": "Import", "tax_category": "Importation", "custom_file_number": "F9"},
       "PO-LOC": {"custom_order_type": "Local", "tax_category": None, "custom_file_number": None},
       "PO-BLANK": {"custom_order_type": None, "tax_category": None, "custom_file_number": None}}


def pi_doc(**kw):
    base = dict(name="PI-1", docstatus=0, status="Draft", supplier="S", supplier_name="S", currency="USD",
                conversion_rate=1, company="VCL", custom_purchase_invoice_type="Local Purchase", bill_no=None,
                bill_date=None, posting_date="2026-10-01", due_date=None, update_stock=0, net_total=0, base_net_total=0,
                grand_total=0, base_grand_total=0, outstanding_amount=0, taxes=[], payment_schedule=[],
                custom_container_details_table=[],
                items=[Row(name="i1", item_code="A", item_name="A", qty=1, uom="Nos", rate=1, amount=1,
                           warehouse="W", expense_account="E", purchase_order="PO-IMP")])
    base.update(kw)
    return Doc(**base)


def stub_pi_helpers():
    saved = {n: getattr(pi, n) for n in ("_payments", "_attachments", "_qbo", "_receive_from", "_suggested_terms")}
    pi._payments = lambda n: []
    pi._attachments = lambda n: []
    pi._qbo = lambda d: None
    pi._receive_from = lambda d: None
    pi._suggested_terms = lambda d: (None, None)
    return saved


class PurchaseInvoiceFollowsPo(unittest.TestCase):
    def setUp(self):
        self.saved = stub_pi_helpers()
        self.doc = pi_doc()
        frappe.get_doc = lambda dt, name=None: self.doc
        frappe.get_all = lambda *a, **k: []
        frappe.db.get_value = lambda dt, name, fields=None, as_dict=False, **k: (
            Row(POS[name]) if dt == "Purchase Order" and name in POS else None)

    def tearDown(self):
        for n, f in self.saved.items():
            setattr(pi, n, f)

    def test_draft_type_follows_the_po(self):
        p = pi.pi_page("PI-1")
        self.assertEqual((p["invoice_type"], p["is_import"], p["type_follows_po"]), ("Importation", True, True))

    def test_local_po_makes_a_local_invoice(self):
        self.doc["items"][0].purchase_order = "PO-LOC"
        self.doc["custom_purchase_invoice_type"] = "Importation"
        p = pi.pi_page("PI-1")
        self.assertEqual((p["invoice_type"], p["is_import"], p["type_follows_po"]), ("Local Purchase", False, True))

    def test_po_that_says_nothing_keeps_the_saved_type(self):
        self.doc["items"][0].purchase_order = "PO-BLANK"
        p = pi.pi_page("PI-1")
        self.assertEqual((p["invoice_type"], p["type_follows_po"]), ("Local Purchase", False))

    def test_no_po_keeps_the_saved_type(self):
        self.doc["items"][0].purchase_order = None
        self.assertFalse(pi.pi_page("PI-1")["type_follows_po"])

    def test_submitted_invoice_keeps_its_saved_type(self):
        self.doc["docstatus"] = 1
        p = pi.pi_page("PI-1")
        self.assertEqual((p["invoice_type"], p["type_follows_po"]), ("Local Purchase", False))

    def test_save_forces_the_derived_type(self):
        pi.pi_save("PI-1", {"custom_purchase_invoice_type": "Other", "bill_no": "X1"})
        self.assertEqual(self.doc["custom_purchase_invoice_type"], "Importation")
        self.assertEqual(self.doc["bill_no"], "X1")

    def test_save_without_a_po_takes_the_payload_type(self):
        self.doc["items"][0].purchase_order = None
        pi.pi_save("PI-1", {"custom_purchase_invoice_type": "Other"})
        self.assertEqual(self.doc["custom_purchase_invoice_type"], "Other")


class BillFromPo(unittest.TestCase):
    def setUp(self):
        self.rate = 130.5
        self.new = pi_doc(conversion_rate=1, custom_file_number=None, name="PI-NEW")
        self.new.insert = lambda: None
        self.new.pop("items")
        self.looked = []

        def get_exchange_rate(frm, to, date):
            self.looked.append((frm, to, date))
            if isinstance(self.rate, Exception):
                raise self.rate
            return self.rate

        for name in ("erpnext", "erpnext.buying", "erpnext.buying.doctype", "erpnext.buying.doctype.purchase_order",
                     "erpnext.setup"):
            sys.modules[name] = types.ModuleType(name)
        m = types.ModuleType("erpnext.buying.doctype.purchase_order.purchase_order")
        m.make_purchase_invoice = lambda po: self.new
        sys.modules[m.__name__] = m
        u = types.ModuleType("erpnext.setup.utils")
        u.get_exchange_rate = get_exchange_rate
        sys.modules[u.__name__] = u
        frappe.get_cached_value = lambda dt, name, f: "KES"
        self.po = "PO-IMP"
        frappe.db.get_value = lambda dt, name, fields=None, as_dict=False, **k: Row(POS[self.po])

    def bill(self):
        return pi.bill_from_po(self.po, bill_date="2026-09-01")

    def test_import_po_sets_type_file_and_todays_rate(self):
        out = self.bill()
        self.assertEqual(out["invoice_type"], "Importation")
        self.assertEqual(self.new["custom_file_number"], "F9")
        self.assertEqual(out["conversion_rate"], 130.5)
        self.assertEqual(self.looked, [("USD", "KES", "2026-10-01")])
        for k in ("name", "docstatus", "po"):
            self.assertIn(k, out)

    def test_uses_the_invoice_posting_date_when_set(self):
        self.new["posting_date"] = "2026-09-15"
        self.bill()
        self.assertEqual(self.looked[0][2], "2026-09-15")

    def test_existing_file_number_is_kept(self):
        self.new["custom_file_number"] = "MINE"
        self.bill()
        self.assertEqual(self.new["custom_file_number"], "MINE")

    def test_failed_or_zero_lookup_keeps_the_po_rate(self):
        self.new["conversion_rate"] = 128.0
        self.rate = 0
        self.assertEqual(self.bill()["conversion_rate"], 128.0)
        self.rate = RuntimeError("no rate")
        self.assertEqual(self.bill()["conversion_rate"], 128.0)

    def test_company_currency_invoice_is_not_looked_up(self):
        self.new["currency"] = "KES"
        self.po = "PO-LOC"
        out = self.bill()
        self.assertEqual(self.looked, [])
        self.assertEqual((out["invoice_type"], out["conversion_rate"]), ("Local Purchase", 1.0))

    def test_po_that_says_nothing_leaves_the_type_alone(self):
        self.po = "PO-BLANK"
        self.new["currency"] = "KES"
        self.new["custom_purchase_invoice_type"] = None
        self.assertIsNone(self.bill()["invoice_type"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
