# Permanent reel tags and physical receiving

This increment prepares labels before arrival and records arrival without posting stock.
It adds the standard `Reel Intake` DocType (module `Reel Tracking`) during migrate.
No fixture, migration or GET request creates tag records or changes stock.

## Global Paper Link 3956

Source: PI `ACC-PINV-2026-00672`, PO `PUR-ORD-2026-00273`, shipment `SHIP-2026-0051`, BL `COSU6470305350`.

Prepare by packing-list size: 93 x 610 mm, 24 x 900 mm, 10 x 1010 mm; all 48 GSM.
127 reels, 54,072 kg supplier net, 54,199 kg supplier gross. Individual weights are unknown.
Both containers remain shipment references; per-reel container assignment is optional.
Never distribute aggregate weights evenly across the reels.

`register_sizes` reserves permanent IDs transactionally and retries return the same records.
It creates zero-quantity Batches for already tracked items, or enables tracking only where there
is no stock history. Legacy items retain their reserved IDs in Reel Intake until their transition
is reviewed. Batch creation uses the same ID and references the Intake for its company context.

The 610 mm item has a recorded legacy balance of 53.851 tonnes. This release does not change it,
enable its Batch flag, change any stock UOM, migrate old stock, submit receipts, or consume stock.
The 900/1010 mm items had no stock history at inspection. Their stock UOM remains Tonne.

## Deployment and operation

1. Deploy `main` of vcl_procurement and vcl_compass together on Frappe Cloud.
2. Run site migrate. The new DocType must exist before preparation is used.
3. Open `/compass/reel-deliveries/ACC-PINV-2026-00672?shipment=SHIP-2026-0051`.
4. Check the three counts/widths/GSM and choose Prepare permanent tags.
5. Print 127 pairs: 254 labels at 125 x 125 mm, or 32 A4 sheets with four pairs each.
6. Use only Compass for new IDs after tag reservation; the legacy intranet is a separate issuer.
7. Match tags by size at unloading; copy the supplier's reel number and label net/gross separately.
8. Confirm the actual VCL arrival date and warehouse individually, or select checked reels in the checklist.
9. Write the matching received-on date on the physical tag. Keep discrepancies on hold.

Scanning `/compass/reels/<ID>` only reads a record. Authentication returns to the same URL.
Reprinting retains the same ID and QR. No prices are returned by these receiving APIs.
Users require Purchase User/Manager, Stock Manager or System Manager and native source-PO read
permission, including Company restrictions. Preparation itself requires a purchasing role.
Warehouse selection is restricted to active warehouses in the source company.

## Stock posting boundary

Arrival confirmation is physical evidence, not a posted Purchase Receipt. The screen explicitly
shows this distinction. Supplier-declared net, supplier-declared gross, warehouse gross and core
are separate fields. Received-on is not inferred from invoice dates, port arrival or ETA.

Full registered-tag stock posting still needs the Tonne-stock conversion path and the 610 mm legacy
transition. It must reuse the reserved IDs. Do not run the older per-reel receiver to allocate a
second identity set for the same physical reels. Detailed issue/return/consumption is a later increment.

Backend tests cover size counts, immutable retry identities, legacy-history protection, role gates,
weight separation, physical dates, stale form refusal, holds and bulk cross-delivery selection.
Frappe bench integration remains a post-deployment check; local tests use a Frappe harness.

## A4 printing and full receiving list

Compass defaults to A4: four rows per sheet, each with an identity label and its matching
movement log side by side. Each compact label is 100 x 71.75 mm, with six handwritten
movement rows. A4 uses shared cut lines, no gutters and a 5 mm outer printer margin;
trim the outer edges, cut once down the centre, then cut across the three row boundaries. The dedicated label-printer option remains 125 x 125 mm per label.
Print at Actual size / 100%, without browser headers or footers.

The delivery header's **Print full receiving list** always includes every registered reel,
regardless of the material/container filters used for label printing. It prints 16 rows per
A4 landscape page, grouped by material/width/GSM (nine pages for shipment 3956), prefilled
with permanent VCL IDs. Unknown supplier markings, weights and actual arrival dates stay
blank. Supplier net/gross are separate from measured gross. Invoice quantities are controls,
not measured warehouse quantities. Its delivery QR opens the checklist without recording arrival.

**Open supplier packing list** opens a readable packing-list attachment on the source invoice.
Private files retain normal Frappe authentication/permissions. Commercial invoices are not listed.

Deploy both apps and run migrate to install these native Purchase Invoice Print Formats:

- `VCL Reel Labels - A4 (4 pairs)` — portrait, 32 pages for 127 reels.
- `VCL Full Reel Receiving List` — landscape, nine pages for this size split.

In ERPNext open ACC-PINV-2026-00672, choose Print, select the desired format, turn Letter Head
off, then print or download PDF. The formats set their own A4 orientation and zero outer
margins, with safe internal margins. They read existing Reel Intake IDs; they never allocate tags,
confirm arrival, submit a receipt, or print invoice prices. Normal receiving role and source-PO
permissions are enforced. The migration creates only Print Format metadata; no stock data changes.
For this shipment the 127 IDs already exist: do not prepare another set. Both formats also refuse
to print for a cancelled source invoice or a delivery with no prepared tags.
