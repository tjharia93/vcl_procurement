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
