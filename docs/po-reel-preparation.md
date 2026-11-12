# Step 2: expected reel preparation from a Purchase Order

For an open submitted PO, `vcl_procurement.po_reel_intake.order_page(po)` reads
the existing `custom_no_of_reels` fields. `register(po)` reserves one permanent
Reel Intake ID for each documented reel and links it to the exact PO child row.
Clients cannot supply replacement counts or item codes. A PO row lock serializes
preparation; retries reuse the original identities. A changed plan or existing
invoice-based tags stops preparation for review. The invoice preparation route
uses the same PO lock and refuses a second set when PO tags already exist.

The invoice link is optional on Reel Intake; the PO remains required. Deploy
vcl_procurement and run **site migrate** before using the new endpoint, so the
source PO line field and optional invoice requirement are installed. Deploy the
matching vcl_compass build for the PO menu and preparation screen.

Open the PO in Compass → More → **Reel tags (from PO)**. The screen shows the
documented counts before the explicit Prepare button and lists the stored IDs
afterwards. Each ID opens its permanent `/compass/reels/<ID>` route. PO-only
records have a read-only preparation view for this increment.

For PUR-ORD-2026-00288-1, the source counts are 20 × PP White 110 mm,
10 × PP White 116 mm, 6 × Thermal 160 mm, 16 × Thermal 240 mm,
and 4 × Thermal 300 mm: 56 reels. The PO quantities remain in Square Meter.
Unknown PP GSM, supplier numbers, invoice numbers, reel weights, actual receipt
dates and warehouses are not guessed. No fixture or deployment creates these
records automatically; a purchasing user must explicitly prepare them.

Preparation creates **only Reel Intake records**. It does not enable Batch
tracking, create Batches, change Item masters, submit vouchers, or post stock.
Label printing for PO-only tags and their subsequent receipt/posting are later
steps; the existing invoice capture endpoint refuses PO-only tags explicitly.
The same permanent identities must be retained when those steps are added.

Validation uses a local Frappe harness for count/line linkage, retries, changed
plans, permissions, invalid inputs, invoice duplicate refusal and read-only
record views. A live bench migration and registration remain deployment checks.
