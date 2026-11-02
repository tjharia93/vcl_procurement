# MEMORY — read this first

Last updated: **2026-10-31**

## State
- Version in `__init__.py`: 1.1.2.
- What is live on the site: unknown. No commit records a deploy.
- Migrate pending: unknown. `add_40hc_container_size` (27/10) needs a migrate, per
  `PURCHASING_PORT.md` and commit 5d32ede. Whether it has run is unknown.
- Merged to main 28/10, deploy unknown: PO lines link to their open Material Request
  (8cea57d); import taxes due the earlier of landing+N days and 30 days from invoice date (f7c35a0).
- The Compass purchasing screens call `pi.tax_preview`, `pi.pi_set_ship` and the new `pi_page`
  keys, so this app must deploy with or before the matching `vcl_compass` change.
- Tests: every unit test module passes on main (checked 28/10/2026, no bench).
- Open branches on origin: `claude/purchasing-defects`, `feat/raise-po`, `po-price-guard`,
  `drop-import-local-filter`, `fix/po-order-date-type`, `qbo-push-page-export-20260616`,
  `store-qbo-last-updated`. Whether any hold unmerged work: unknown.

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
