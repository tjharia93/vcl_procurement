# Purchasing port: /purchasing-dev -> Compass

Between 29 and 30 September 2026 the purchasing screens were prototyped as a
Frappe Web Page (`/purchasing-dev`) on the live site. This app now holds the
server side of that prototype; the `vcl_compass` app holds the screens.

## What moved here, and where
| Rule / feature | Module |
|---|---|
| Business rules (dates, KES, schedule, gate, units, search, tax rows) | `api/rules.py` (pure Python, unit tested) |
| Item / supplier search (any words, any order), units | `api/search.py` |
| Purchase order: page, edit a draft, tax template and rows, workflow actions | `api/po.py` |
| Purchase invoice: page, edit, submit, payments, KRA gate | `api/pi.py` |
| Open / draft order lists (one call, with connections) | `api/purchasing.py` |
| Raise a PO, complete in one transaction | `api/raise_po.py` |

## Decisions
- **Import invoices need bill date, KRA customs entry date and KRA customs entry number to be submitted.** Enforced on the server: `pi_submit` and a `before_submit` document event, so the Desk is held to it too. Applies to type `Importation` only.
- **Ledger date of an import invoice = KRA customs entry date.** Due date still runs from the supplier invoice date plus terms.
- **Import payment schedule is two rows:** net total per the supplier's payment terms (from the bill date); taxes due 2 days (adjustable) after the vessel lands.
- **Ledger only, pending receipt** is the default: `update_stock` off posts Dr 2210 Stock Received But Not Billed. Ticking Receive goods sets `update_stock`.
- **Taxes are typed in KES**; ERPNext stores the document-currency figure (KES / rate).
- **The price rule still holds:** a line leaves the API as `rate` and `amount` only; a save takes `rate` only.
- **Local Purchase invoices are not submitted from this API.** Their CUIN checks live in the Desk client script and `vcl_kra_validation`.
- This supersedes the 4 Sep 2026 line "purchase invoices stay in the Desk" for IMPORT invoices, by Tanuj's instruction of 29-30 Sep.

## Not in this app (master data changed by hand on 30 Sep 2026)
`Importation - VCL` tax template: Import Duty, VAT, IDF Fees and RDL changed from
"On Previous Row Amount" to "On Previous Row Total" (they had produced 0 on every
new import PO since 21 Sep). Old rows: `backups/` of the prototype. Open question:
VAT's base excludes import duty because it points at the insurance row.

## After deploy + migrate
1. `python3 scripts/smoke_purchasing.py` (read-only).
2. Confirm on a draft import invoice with the KRA entry date empty that the Desk's Submit is refused.

Custom fields `custom_kra_entry_date` and `custom_file_number` (Purchase Invoice) exist already; the patch and `after_install` create them on any other site.

## Added 01/10/2026
Additive contract changes for the ported Compass screens (no existing key removed):
- `rules.py`: `invoice_type_for_po`, `plain_text`, `html_from_text`, `is_stale`, plus `check_order_date`, `ordered_summary`, `rate_after_lookup`.
- `po_page`: line `description` (plain text), page `note` (`custom_comments__`). `po_save`: `order_date`, `note`, per-line `description` (written only when changed). `create_purchase_order`: `order_date`, `note`, per-line `description`.
- `search_items`: `description` on each hit.
- `open_orders` / `draft_orders` rows: `stale`, `ordered {text, more}`, `order_confirmation_no`, `file_no` (drafts also `confirmation_no`).
- `container_search`: hits carry `kind` ('container' | 'bl'); BL matches on `custom_bill_of_lading_number`; cancelled invoices excluded. `pi_list` rows: `bl_no`.
- `pi_page`: a draft's `invoice_type`/`is_import` follow its PO, plus `type_follows_po`; `pi_save` forces that type. `bill_from_po`: sets type and file number from the PO, refreshes the exchange rate, returns `invoice_type` and `conversion_rate`.

## Added 27/10/2026 (Purchasing defects triage)
Additive; no key removed. One patch (`add_40hc_container_size`): **migrate needed**.
- `create_receipt`: `lines` is `{PO line idx: qty}` and is matched to the mapped receipt by `purchase_order_item`, never by the receipt's own idx (it renumbers the pending lines from 1). Fixes a partial receipt landing on the wrong line when an earlier line was already received (pur-def-receipt-idx). `rules.receipt_qty_by_row`.
- `receipt_preview` items: `description` (plain text, blank when it only repeats the item name).
- `pi_save`: `remove_lines: [row name]` drops invoice rows the supplier did not bill (at least one stays); `custom_acd_reference_code`, `custom_export_entry_received`; `shipment_record {vessel, voyage_no, bl_date, idf_number, port_of_loading, packing_list_gross_kg, packing_list_net_kg, packing_list_packages}` written to the invoice's Import Shipment through `vcl_imports.api.shipments.update_shipment`. When the schedule is not rebuilt, its amounts are re-fitted to the new totals (`rules.refit_schedule`: import net/taxes rows, or one row = the whole invoice).
- `tax_preview(name, payload)`: ERPNext's own calculate_taxes_and_totals on an unsaved copy with the edit applied; the screen's preview now comes from here.
- `pi_set_ship(name, fields)`: ACD code / export tick, replacing the screen's REST write.
- `pi_page`: `file_no` falls back to the proforma (`file_no_source`); `shipment.days_free` falls back to the shipment's free days (`days_free_source`); `shipment.record` (the Import Shipment: BL match first, else the order's only shipment), `shipment.records_on_order`, `shipment.acd_code`, `shipment.export_received`; `lines[].po_detail`; `qbo_memo_standard`; `total_tax_kes` is now base grand total - base net total.
- `qbo_prepare`: every invoice's queue memo carries the order and the ERPNext invoice (blank memo -> the standard one; a typed memo lacking them gets them appended).
- `container_search`: also Import Shipments by BL (`kind: 'shipment'`, `invoice: null`, `shipment`).
- `schedule_preview`: also takes the edit keys (`lines`, `remove_lines`, `taxes`, `conversion_rate`) and works the amounts due out on them, so the Due dates preview follows the lines as typed.

## Added 09/11/2026 — Dev parity (native React)
- D1: recent payment drafts are reused only when every account, currency, date, reference, rate,
  allocation, term and remark matches, with write permission on the candidate.
- D2: `pi_page`/`pi_list.outstanding` and payment `applied` are in invoice currency. Additive
  `outstanding_account` and `party_account_currency` preserve the source balance and its basis.
  Payment ceilings and ERP payment mapping use the matching currency.

- D4/D5: draft type is normalized from linked POs before submission gates (including Desk);
  an import with posting date different from customs entry date must be saved before submit.
  The global gate also preserves the duplicate-stock-receipt guard.
- D9: workspace posting-date selection refreshes foreign-local FX on that date. PI save also
  refreshes FX when the posting date changes without an explicit rate; missing FX refuses save.

- D7: MR lines include actual `stock_uom` and resolved `conversion_factor`; create re-resolves known MR factors.

- A2: PO page lines return `cost_center`/`expense_account`; create and save accept both per line. `po.line_context` provides permission-aware company picks and item defaults.

- A4/A5: `po_save.conversion_rate` accepts positive FX and converts Actual taxes at the new rate. PO create/save accept file numbers for local orders too.

- A1: `po.recent_orders(supplier)` returns five permission-filtered non-cancelled orders; repeat uses the existing checked `po_page` API.

- A6: PR page lines add `name`. `pr.pr_save(name,payload)` saves draft date/delivery note/line qty/rate together, preserving PO links. Reel quantities remain owned by packing-list receiving.

- A7: `attachments.list_files(doctype,name)` reads PO/PR/PI files after document read permission. Uploads remain private through Frappe upload_file.
