# Bulk existing reel Web Page

Live route: https://vimitconverters.frappe.cloud/reel-stock-bulk-dev

ERPNext Web Page document: `bulk-existing-reel-registration`.
Edit `main_section_html` using `page.html`, and `javascript` using `page.js`.
These are saved Web Page fields; they do not require an app release, asset build or migration.
No fixture installs or overwrites this page during later app deployments.

The page uses the already-deployed `vcl_procurement.tagged_stock` API. Shared material,
GSM, optional brightness, width, company, actual warehouse and physical date apply to
all rows. Each reel gets its own measured net weight or an explicitly unknown weight.
Use a separate batch for different shared specifications. Maximum 200 rows per batch.

This page is for **untagged, pre-existing physical reels**. It never allocates IDs on
load, review or print. The operator must confirm physical inspection before registration.
Each save calls the existing `register` endpoint with `count=1`, its own UUID reference,
the exact reviewed plan and `physically_verified=1`. It does not post ledger stock.
Role, company, warehouse, date and transaction validation remain on the existing server.

Rows commit separately. A failed or uncertain request stops the batch and retains the
same reference and plan for retry. Completed rows are skipped; a lost response is safely
replayed through the server's idempotency check. Per-user browser storage and downloadable
JSON backups preserve those references. Restoring a backup performs no registration and
refuses reviewed plans that differ from the displayed values. Do not replace an uncertain
row with a newly generated reference.

Labels are fetched individually through the server's authorized `print_labels` endpoint.
The page combines the existing label sheets into one inline, sandboxed print preview;
each QR retains `/compass/stock-tags/<permanent ID>`. No client renderer duplicates the
server's dimensions, brightness, weight or purchase-provenance rules.

`check.cjs` runs an offline browser verification with every request intercepted. It covers
decimal validation, unknown weights, physical confirmation, a committed request with a
lost response, reload/retry without duplicate IDs, backup restore and tamper rejection,
multi-label print, mobile width and guest handling. It creates no production records.
Run it with a Node environment containing Playwright and an installed Edge browser;
adjust the local Playwright dependency path if using another workstation.

First publication: 13 November 2026. Live verification checked the authenticated page,
permission-filtered companies/warehouses, date, empty registration table and CSRF
initialization. Real registration was intentionally left to checked stock and the operator.
