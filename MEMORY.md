# MEMORY — read this first

Last updated: **2026-11-17**

## State
- Version in `__init__.py`: 1.1.2.
- What is live on the site (checked by API, 17/11/2026 evening): main at `e85085b`. `worklist`,
  `sheet_page` and `item_search` answer on live, and they exist only from that commit.
- Migrate pending: none (17/11/2026). The Phase S DocTypes and Physical Stock Unit fields were
  synced at 06:22 on 17/11. Every patch in `patches.txt` is in the live Patch Log, including
  `add_40hc_container_size` (27/10).
- Merged to main 28/10, deploy unknown: PO lines link to their open Material Request
  (8cea57d); import taxes due the earlier of landing+N days and 30 days from invoice date (f7c35a0).
- The Compass purchasing screens call `pi.tax_preview`, `pi.pi_set_ship` and the new `pi_page`
  keys, so this app must deploy with or before the matching `vcl_compass` change.
- Tests: every unit test module passes on main (checked 28/10/2026, no bench).
- Open branches on origin: `claude/purchasing-defects`, `feat/raise-po`, `po-price-guard`,
  `drop-import-local-filter`, `fix/po-order-date-type`, `qbo-push-page-export-20260616`,
  `store-qbo-last-updated`. Whether any hold unmerged work: unknown.

## Import costs: customs entry, landed cost, deposit — 2026-11-27 (branch `claude/import-customs-landed-cost`, NOT merged, NOT deployed; MIGRATE needed)
- New `api/import_costs.py` (+ pure `api/import_rules.py`; tests `tests/test_import_rules.py` 37, `tests/test_import_costs.py` 14). Screen: vcl_compass `ImportCostsPanel` on the import invoice (same branch name; also a small vcl_imports branch).
- Tanuj, 26/11/2026: the import invoice stays a draft until the KRA customs entry gives the rate; KRA charges go by Journal Entry, not the invoice's taxes table; VAT never into stock; levies and CFS / clearing / shipping line charges into stock by Landed Cost Voucher, spread by quantity; a deposit is a receivable.
- **Off by default.** Site config `vcl_import_customs_entry`: absent/0 off, "draft" = drafts only, 1 = posts. `vcl_import_post_for` (list of invoice names) may post while the site is in "draft". `vcl_import_vat_account` has no default and must be set before a customs entry with VAT can be saved.
- Whitelisted: `import_page` (read), `customs_save` (draft JE + customs rate, entry no. and date on the DRAFT invoice; refuses if the invoice still has taxes), `customs_post` (JE then invoice, one savepoint), `landed_create` (draft LCV over the order's submitted receipts, distribute by Qty), `landed_post`, `deposit_recover`, `gate_check`. Writes are Finance only (Accounts Manager / Accounts User / System Manager).
- Receiving lock: `assert_can_receive(po)` in `purchasing.create_receipt` and `reels.receive_reels`, and `lock_before_submit` on Purchase Receipt. Active only when the switch is 1 and the order's `custom_order_type` is Import.
- Migrate adds (patch `add_import_cost_fields`): Journal Entry `custom_import_invoice`, `custom_import_entry_kind`, `custom_customs_entry_no`; Purchase Invoice `custom_customs_exchange_rate`, `custom_customs_journal_entry`.
- Landed cost "already booked" is read per expense account from vouchers on the same receipts. A voucher that mixes two shipments' receipts would confuse it.
- A deposit held in a foreign-currency account (1652 is USD) is not recovered here; the screen says to use ERPNext.
- Never run on a bench. Unknown until it is: that `Landed Cost Voucher.get_items_from_purchase_receipts` and the Journal Entry rows save as written on ERPNext v16, and that EIV / 5113 are the right accounts to credit. Not built: QBO mapping for the capitalised charges; seal numbers at the gate (no field holds them).

## Scan screen /scan — 2026-11-25
- `www/scan.html` + `www/scan.py` (route `/scan`, signed-in only; Guest → /login?redirect-to=/scan). Lives in this app, not Compass, because Compass deploys from `asset-transfer-note-live-20261118`, which holds other undeployed work. Move it into the Compass SPA later if wanted.
- `scan.resolve(code)` (POST): `scan_core.normalize` reads ID-only and old link QRs; looks in Reel Intake, then Physical Stock Unit, then Batch (VCL-RL); Stock Bundle then unit (VCL-BND); Stock Container then unit (VCL-IC). Shown only if `frappe.has_permission(doctype,'read',doc)`; otherwise the same "Not found or not yours to see". No supplier or price fields are read. Logged to `frappe.logger('vcl_scan')`; 120 look-ups a minute per user. LOC- codes answer "not built yet".
- Those doctypes are readable only by Stock Manager, Purchase User/Manager, System Manager. A store user with only Stock User sees "Not found" until given a role or read permission.
- Camera: native BarcodeDetector where the browser has it, else the bundled `public/js/jsQR-1.4.0.js` (Apache-2.0, from npm jsqr@1.4.0). No outside service.

## Reel label privacy — 2026-11-25
- Tanuj, 25/11/2026: the 100 x 70 reel label (`reel_identity_print.labels_html`, every reel print path) no longer prints supplier, PO, BL, container or the "Bought on" source text. Its QR holds only the reel ID (`id_qr`), not an ERP URL; caption "SCAN IN COMPASS".
- Bottom strip: MILL REEL NO. (supplier reel no., when known; used to match labels to reels at offloading) and RECEIVED (dd/mm/yyyy, or a blank write-in line). Existing stock with a "Bought on" note shows the date only.
- Tanuj, 25/11/2026: "Remove vimit name". The reel label's top line is now "REEL", both bundle labels say "BUNDLE", no logo mark. The ink/chemical container label (stays in-house) and internal A4 sheets keep the name.
- Builders still pass `qr_uri` (URL QRs); the reel label ignores it. Bundle, container and stock-tag labels are unchanged.
- Scanning a new reel label with a phone camera shows only the ID; nothing opens the record until the Compass scan screen is built. Old URL labels keep working.
- `tests/__init__.py` installs a fake `pyqrcode` (writes the QR text) when the real one is missing, so tests can decode the QR.

## Existing stock worklist Web Page — 2026-11-17 (LIVE 17/11/2026)
- `dev_pages/existing-stock/` (route `existing-stock-dev`): the screen for B.1/B.2. Per tagging sheet: print, record signed ticks, link items (Tanuj), preview and create drafts (Tanuj). README says how to create the Web Page.
- New read-only methods in `existing_stock.py`: `worklist`, `sheet_page`, `item_search` (+ pure `sheet_summary`, `group_sheets`, `item_fits` in `existing_stock_core.py`). Deployed with `e85085b`, no migrate needed. 21 tests pass; `check.cjs` passes offline.
- **Web Page created 17/11/2026** by REST: name `existing-stock-into-the-books`, route
  `existing-stock-dev`, published, `dynamic_template` 0. Keep it at 0: the CSS uses `{#...`
  selectors, which Jinja reads as a comment. Later edits go to the `main_section_html` and
  `javascript` fields, and browsers may cache the page for 5 minutes.
- Checked live as Tanuj: 11 sheets, 10 at Tag and sign. `EXIST-BULK-1610717d…` (11 newsprint reels, Warehouse 5, signed by PARAG 17/11) waits at Link items.
- `check.cjs` on Windows: no Playwright browsers are downloaded. Run it with `PLAYWRIGHT` pointed at an installed playwright module and launch `channel: 'chrome'`.

## Existing stock into the books, Phase S writes — 2026-11-16 (deployed 17/11/2026)
- New `existing_stock.py` (+ pure `existing_stock_core.py`, tests `tests/test_existing_stock.py`, 14 pass). Spec: `C:\Second_Brain\output/tagged-stock-workflow/tagged-stock-build-plan.md`, Phase S; Tanuj's decisions 16/11.
- Phase S is now B.1 (tagging sheets, item links) and B.2 (opening and transfer drafts) in the renumbered plan; B.3 is the first real use. Tracker: https://claude.ai/artifact/LuSLRTPUTB2yt86KoAobEw
- **Deployed 17/11/2026**: Frappe Cloud (Apps tab) shows vcl_procurement at main `db59a0a`, which includes `15d584e` and `eea7cb4`. The migrate has run: both DocTypes and the unit fields were checked by API on 17/11. B.1 and B.2 stay in review until one real registration has been through them.
- 17/11 (eea7cb4): tagging sheets key on the bulk batch (`EXIST-BULK-<batch>-<row>`), one sheet per batch. No schema change.
- The migrate adds: 9 new fields on Physical Stock Unit; new DocTypes `Tagging Sheet Return`, `Existing Stock Draft`; doc_events on Stock Reconciliation / Stock Entry (submit, cancel, trash) keep linked units' `stock_draft_status` true.
- Whitelisted: `permissions`, `tagging_sheet` (A4, tick boxes + signature), `draft_preview` (GET, read only); POST `set_item`, `record_sheet`, `prepare_draft`.
- `set_item` and `prepare_draft` are Tanuj-only: `frappe.conf.tagged_stock_item_approvers`, default `tanuj.haria@vimit.com` (+ Administrator).
- Opening = draft Stock Reconciliation, purpose Opening Stock, expense = `get_difference_account` (1910 Temporary Opening), refused if the warehouse already holds the item. Batched item: one row per unit, batch = tag ID. Transfer = draft Material Transfer from the book warehouse; batched items refused (pick batches in Desk). m² items never map per reel. Nothing submits.

## Purchase Invoice change requests — 2026-10-31 (branch `claude/pi-change-requests`, NOT merged)
- Server half of vcl_compass's same-named branch; deploy this app first. No DocType, no patch, no migrate.
- New whitelisted: `pi.kra_check`, `pi.bill_totals`, `pi.bill_save`, `pi.qbo_history`, `pi.cancel_review`, `pi.pi_cancel`, `search.purchasing_search`. Changed: `pi.qbo_prepare(name, date_reviewed=0)` refuses a local bill whose date needs review; `pi_page` adds `receipt_check` and `qbo.stage_hint`; `bill_preview` adds quantities, terms, drafts; lists add `created`.
- Pure rules (tested in `tests/test_rules.py`, wiring in `tests/test_cr_1031.py`): `bill_date_review` + `DATE_RULE_POLICY` (the one place for Tanuj's open policy questions a–d), `loose_date`, `dmy`, `stage_hint`, `receipt_cover`, `billed_qty`, `mapping_history`, `doc_search`, `cancel_check`.
- `staging_enabled` in QBO Sync Settings defaults to 0: submitted PIs are not auto-staged until it is ticked.
- `tests/test_port_api.py` already had 2 failing tests before this branch (get_meta stub, transaction_date type).

## What happened in dev
- 2026-10-28 Import payment schedule: taxes due the earlier of landing + N days and 30 days from
  the invoice date; the Taxes row carries the Import Taxes payment term (TRK-00147).
- 2026-10-28 `create_purchase_order` links any unlinked PO line to the single open MR line for
  the same item with room for the qty. `link_material_requests(po)` repairs an existing PO.
  Cause: MAT-MR-2026-00079 stayed 0% ordered after PUR-ORD-2026-00270.
- 2026-10-27 Purchasing defects triage: receipts matched by PO line; remove unbilled PI lines;
  server tax preview (`tax_preview`); `pi_set_ship`; voyage facts written to the Import Shipment
  through `vcl_imports`; BL finder finds shipments; QBO memo carries order and invoice; 40 HC
  container size patch; schedule preview follows the edit as typed.
- 2026-10-24 Phone: material requests from draft to receipt (`mr.py`), a purchasing `feed(since)`,
  plain Submit for a PO on a site without a workflow, `po_mark_shared`, newest done orders first.
- 2026-10-24 `track_changes` off on QBO Sync Settings (139,033 Version rows, about 900 MB).
- 2026-10-15 PI before_submit runs both the KRA gate and the ACD gate (one had been dropped).
- 2026-10-13 Email when a QBO push waits over 30 minutes (cron every 15 min); Expense / typed-VAT
  fields on the push queue, editable after submit so a failed push can be retried.
- 2026-10-12 Bill from PO re-counts the payment schedule from the supplier invoice date.
- 2026-10-09 ACD reference code and Export Entry Received required before an Importation PI is
  submitted; Monday 07:00 email chases imports with no ACD code.
- 2026-10-02 QBO push: an import is dated on its KRA customs entry date and numbered by its KRA
  customs entry number; PI page shows the queue row and the review.
- 2026-10-01 Paper reels received one Batch at a time; line descriptions, order note, order date,
  stale flags, BL search, invoice type follows the PO.
- 2026-09-29 to 09-30 Purchasing port from `/purchasing-dev`: rules as pure Python with tests,
  PO / PI / receipt / bill APIs, KRA submit gate on the server, Local invoice submit.
- 2026-09-04 Purchasing moved out of `vcl_compass` into this app; one purchasing API for both
  front ends; raise a PO (from an MR too); `rate` made authoritative on PO lines.
- 2026-04-29 to 04-30 Phase 1 QBO Sync scaffold; event-driven staging on PI submit with ToDo
  sidebar; Import/Local filter dropped; `qbo_last_updated_at_push` stored for drift.

## Decisions
- Import invoices need bill date, KRA customs entry date and number before submit, enforced on
  the server so the Desk is held too. Importation type only (29-30/09/2026, Tanuj).
- Ledger date of an import invoice = KRA customs entry date; due date runs from the supplier
  invoice date plus terms (29-30/09/2026).
- Import payment schedule is two rows: net per supplier terms, taxes after the vessel lands
  (29-30/09/2026); taxes capped at 30 days from invoice date (28/10/2026, TRK-00147).
- Default is ledger only, pending receipt (`update_stock` off, Dr 2210 SRBNB) (29-30/09/2026).
- Taxes are typed in KES; ERPNext stores the document-currency figure (29-30/09/2026).
- A PO line leaves the API as `rate` and `amount` only; a save takes `rate` only (04/09/2026).
- Local Purchase invoices are not submitted from this API; their CUIN checks live in the Desk
  client script and `vcl_kra_validation` (29-30/09/2026).
- Items are never created by the purchasing APIs (`search.py`, `raise_po.py`).
- Purchasing logic belongs here, not in `vcl_compass` (04/09/2026, commit a5b3670).
- All submitted PIs since the cutoff are eligible for QBO staging; no Import/Local filter
  (30/04/2026). Staging failures must never block PI submission (`staging.py`).

## Traps
1. A repeated key in the `doc_events` dict keeps only the last. That silently dropped the KRA
   gate until 15/10. Hooks for one event go in a list.
2. A margin left on a PO line is re-applied on the next edit (Labchem ordered at 280 against a
   quote of 250, 4 Sep 2026). `po_price_guard` clears it on every save.
3. A mapped Purchase Receipt renumbers pending lines from 1. Match receipt rows on
   `purchase_order_item`, never on `idx`.
4. `track_changes` on QBO Sync Settings filled the DB (site at 1.99 of 2 GB, 24/10). Keep it off
   in the DocType JSON, or a migrate switches it back on.
5. The `Importation - VCL` tax template was fixed by hand on 30/09 ("On Previous Row Total").
   It is master data, not in this app. Open question: VAT's base excludes import duty.
6. The QBO push runs on the on-prem box. If the box, funnel or n8n is down, queue rows wait
   Pending; `push_watch` emails after 30 minutes and the box sweeps them when back.
7. Debit notes (`is_return=1`) are blocked from staging and need manual QBO entry.
8. README lists 10 DocTypes; there are 11 (QBO Sync Settings is the extra).
9. Two test modules (`test_acd`, `test_push_watch`) fail when run as a script; run them with
   `python3 -m unittest` from the repo root.
