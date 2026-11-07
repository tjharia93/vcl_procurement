# Bundle registration and labels - part 1

This release adds two standard DocTypes in Reel Tracking: Bundle Registration
(one immutable preparation plan) and Stock Bundle (one physical wrapped bundle).
Neither record is a stock balance. It creates no Item, Batch, Stock Entry,
Purchase Receipt, Stock Reconciliation, valuation or transfer checklist.

## Initial Soykimau preparation

The Compass preset is Manila, 160 GSM, 610 x 670 mm, 100 sheets per ream,
five reams per wrapped bundle. Yellow: 36 bundles / 180 reams / 18,000 sheets.
Green: 24 bundles / 120 reams / 12,000 sheets. Total: 60 / 300 / 30,000.
Preparation reference: SOYKIMAU-MANILA-610X670-160-INITIAL.
The user confirmed that this stock is not currently recorded in ERPNext.
No existing 610 x 860 mm Item is reused. Item mapping and valuation are deferred.
Soykimau - Raw Material - VCL and Soykimau - Stores - VCL already exist.
Intended storage is a plan, not the current physical location or a received balance.

## Deploy and use

1. Deploy main of vcl_procurement and vcl_compass; run site migrate.
2. In Compass open Stores > Register and label bundles, or /compass/bundle-registrations.
3. Check company, intended storage, specifications and the 36/24 counts.
4. Choose Register 60 bundles. The transaction creates the registration and 60 IDs
   from the independent VCL-BND-YYYY-##### series. Migrations and GETs never create IDs.
5. Print all 60 or filter by colour. Default: 100 mm wide x 30 mm high thermal labels.
   Print Actual size / 100%, no browser headers/footers. A4 eight-up printing is optional.
6. Attach one matching-colour label to each outer wrapper. Reprinting never changes IDs.
7. Scan the authenticated /compass/bundles/<ID> URL. Check physical contents and record
   the counted number of reams, or put a mismatch on hold with a reason. Scanning only reads.

The same preparation reference and canonical plan return the existing 60 IDs on retry,
including if the request succeeded but its response was lost. Changed packing/counts
under that reference are refused; review the existing run instead of issuing another set.
Other preparations must use distinct references; do not create a new reference for these
same physical bundles. Printing is a GET and never confirms a check or a stock posting.
Native ERPNext print formats are VCL Bundle Labels - 100x30 (Bundle Registration) and
VCL Bundle Label - 100x30 (Stock Bundle). They use the same layout and authorized data.

## Controls and boundary

APIs require Stock Manager, Purchase Manager, Purchase User or System Manager plus
native Company/document permissions. Intended warehouse must be active, a leaf and in
the same company. Standard records have read-only role permissions; APIs own writes.
Company locking serializes first-creation retries; native Series locking allocates IDs.
Bundle checks lock and reload the row and reject stale forms. Verified counts must equal
the prepared packing; a mismatch requires a hold reason rather than changed label contents.
Track Changes retains the inspection history. Re-verification of a hold is explicit.

The label always says stock not posted, including checked bundles. The physical check is
evidence about the bundle contents, not a receipt at Soykimau. Count and inspection fields
do not establish book stock. Stock posting, item/unit mapping, valuation and movement
checklists are later work. The prototype PDF uses SAMPLE identities and a QR containing
prototype text, not a live Compass destination. Do not attach prototype labels to stock.

Local tests use a Frappe harness. After deployment verify real schema permissions, duplicate
retry IDs, a saved check and native PDF generation. Never create a second run to test live IDs.
