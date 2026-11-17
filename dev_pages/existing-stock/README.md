# Existing stock into the books: Web Page (B.1, B.2)

Live route: https://vimitconverters.frappe.cloud/existing-stock-dev (Web Page
`existing-stock-into-the-books`, created 17/11/2026)

The screen for Phase B of the tagged-stock plan. It works through each **tagging sheet**
(one registration, or one whole bulk batch from `/reel-stock-bulk-dev`):

1. **Tag and sign**: preview and print the A4 tagging sheet, then record the signed ticks
   (Present, Not found, Held; a note is required for Not found and Held) with who signed and when.
2. **Link items** (Tanuj only): select units, search for the ERPNext item that holds their balance
   (only items whose stock unit fits are shown, with where the books hold them today), and say
   whether the books already hold them (In books) or not (Missing from books).
3. **Prepare drafts** (Tanuj only): Missing from books becomes a draft Opening Stock reconciliation
   (needs the approved valuation rate per item); In books elsewhere becomes a draft Material Transfer
   from the warehouse where the books hold it. Preview first; nothing submits.

## Server
Uses `vcl_procurement.existing_stock`:
- deployed 17/11/2026: `tagging_sheet`, `record_sheet`, `set_item`, `draft_preview`, `prepare_draft`;
- added with this page, deployed 17/11/2026 (`e85085b`): `worklist`, `sheet_page`, `item_search`, all read only.

Every rule, permission and version check stays on the server. Writes send each unit's `modified`;
a changed record is refused and the page reloads the sheet. Sheet and draft requests carry a
caller-made ID kept in browser storage until the server answers, so a lost response is retried
with the same ID and never records or drafts twice.

## Create the Web Page in ERPNext
New **Web Page**: title `Existing stock into the books`, route `existing-stock-dev`, Published,
Content type HTML. Paste `page.html` into **Main Section (HTML)** and `page.js` into **Javascript**.
These are saved fields; no app release or migrate is needed for later page edits.

## Check
`node dev_pages/existing-stock/check.cjs` runs an offline browser check with every request intercepted
(set `PLAYWRIGHT` to the Playwright module path if needed). It covers the list filters, the note rule,
a lost response on recording with a same-ID retry, item search and linking with the reloaded version,
comma-decimal refusal for rates, preview-before-create, one draft only, the read-only view for someone
other than Tanuj, and no sideways scroll at 390 px. It creates no production records.
