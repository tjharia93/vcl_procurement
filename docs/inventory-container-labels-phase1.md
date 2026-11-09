# Ink and chemical identities and approved label sizes

This increment implements permanent IC identities, PO/invoice shared allocation,
100 x 30 mm container labels, physical arrival checks and 100 x 100 mm reel labels.
Deploy vcl_procurement with vcl_compass and run site migrate. Pushing main alone
does not deploy either app on Frappe Cloud.

## Operator entry points

- Existing Compass PO > More actions > Ink / chemical container tags.
- Existing linked supplier invoice > Container tags (choose its PO where there are several).
- Permanent IC QR destination: `/compass/containers/VCL-IC-YYYY-#####`.
- Delivery preparation: `/compass/container-deliveries/<PO>?invoice=<optional PI>`.

Select an eligible PO line, physical container count and nominal quantity per
container in the item's existing stock unit. The default eligible Item Group is
`Inks and Printing Chemicals`, including descendants. An optional site config
`ic_container_item_groups` list can select other existing groups. This release
does not create/reclassify Items, alter UOMs or enable Batch tracking.

Only Kg and Litre/Liter stock units are accepted; there is no kg/litre conversion.
Nominal total may not exceed the order line's stock quantity. There is one immutable
initial allocation per original PO line. The invoice is resolved to that line,
so PO-first and invoice-first operations reuse the same IDs transactionally.
The PO is locked and reloaded before allocation. A retry with changed initial
packing is refused. Use **Change remaining packing** for an explicit, reasoned
packing revision: retire only untouched Expected identities and allocate new ones,
while arrived or held containers retain their original IDs, quantities and evidence.
The revision can also add the next packing if no unused Expected units remain.
Totals stay within the ordered stock quantity. Replaced records remain readable
for history but cannot print active labels or be checked back into use. Revision
retries reuse their result; stale unrelated changes are refused. Lines with posted
receipts require receipt-link review before revisions in this increment.

Example: for 50 L, two arrived 10 L cans remain unchanged; three unused 10 L tags
are replaced by six new 5 L identities. Future POs can use a different pack size
for the same Litre-stocked material without changing the item's stock unit.
Preparing initial identities for an already received line is refused pending review.

## Printing and arrival

Print from the allocation, or reprint one container from its scanned record.
The labels show the permanent ID, material, supplier lot, nominal pack quantity,
applicable expiry and authenticated Compass QR. Nominal pack is visibly labelled;
the actual checked quantity stays separate on the record. Each physical tin, drum
or can retains its own identity even when several share one supplier lot.

Actual arrival date, physical quantity, supplier lot and actual warehouse are
required for a checked arrival. Expired material must be held; expiry-required
Items need an expiry date. Holds require a reason and preserve captured evidence.
Stale forms are refused. Every physical check retains actor/time and change history.
Scanning and printing are GETs and do not confirm arrival or change stock.

Native opt-in formats installed during migrate:

- `VCL IC Container Labels - 100x30` on Container Allocation.
- `VCL IC Container Label - 100x30` on Stock Container.
- `VCL Reel Labels - 100x100` on Purchase Invoice, one identity and one movement page per reel.

Compass reel label-printer mode now uses 100 x 100 mm. Existing A4 compact printing
is retained and explicitly labelled compact; it is not the exact 100 mm stock option.
Bundle printing remains 100 x 30 mm. Print at Actual size / 100%, with headers and
footers off; verify the physical first print with a ruler and a phone scanner.

## Authorization and stock boundary

APIs require Purchase User/Manager, Stock Manager or System Manager, plus native
source-order, company and document access. Standard identity records expose read
permissions only; controlled APIs own writes. No guest data is returned. Sign-in
returns to the same container or delivery URL.

No receipt, Stock Entry, opening balance or valuation is created. The schema has
future Batch/receipt references but no parallel stock balance. The next increments
must connect accepted identities to stock vouchers, resolve tonne/legacy handling,
and then implement warehouse transfers and job use/return. This release must not
be represented as completed stock/consumption integration.

## Acceptance after deployment

Local harness tests verify shared allocation, retry IDs, permission refusal,
received-line protection, UOM limits, expired/held arrivals, stale forms and
read-only scanning/reprinting. Native bench behaviour still requires deployment
acceptance: schema/role checks, same-ID PO/invoice retry, one saved check and native
PDF rendering. Use one real intended allocation; do not prepare duplicate test runs.
