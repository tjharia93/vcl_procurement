# Paper reel receiving - first Phase 1 release

This release enables the first shipment to be received as physical reels while preserving the PO's tonne quantity and rate. It is the receiving/identity increment of Phase 1; automated checkout, return and job consumption are not included in this increment. The paired handwritten movement log is in Compass.

## Deploy and migrate

Deploy `vcl_procurement` and `vcl_compass` together. Run site migration so `reel_receiving_phase1` creates the new Batch traceability fields and Purchase Receipt retry fields. Existing stock and items are not modified by this patch. Fresh installations use the same field setup through `after_install`.

The site was inspected as Frappe 16.55.2 / ERPNext 16.53.0. Native Serial and Batch Bundles remain authoritative; label lookup supports both direct row batches and bundle entries.

At cutover, stop issuing new reel IDs in the intranet. Its SQLite `paper_reels` table was checked read-only on 2026-11-06: 441 reels, maximum `VCL-RL-2026-00441`. Recheck immediately before the first ERP registration and raise `REEL_ID_FLOOR` if necessary. Frappe Series allocates subsequent IDs under its transaction row lock, seeded above both the legacy floor and existing ERP batches. Preview IDs are not reservations.

## API

- `reel_lines(po)` returns `kg_ok`, actual PO conversion factor and any unsupported-UOM reason. This increment supports Kg stock with Kg or Tonne purchasing; unsupported units are refused explicitly.
- `receive_reels(po, lines, posting_date?, supplier_delivery_note?, invoice?, department?, container?, request_id?)` creates a **draft only**. Quantities entered per reel are kg. Each PO row is split into reel rows using its original UOM and rate. Omitted lines are excluded. Native over-receipt limits are checked before creating Batches.
- Each reel accepts `weight_basis`, declared/gross/core/net weight, supplier reel number, width, GSM, diameter, metres and inspection status. Supplier-declared weight posts the declared quantity without claiming a measured net weight. Verification remains pending.
- Invoice supplier/company/PO references and container selection are validated. No invoice is submitted or has Update Stock changed. A source invoice which already posted stock cannot be received again.
- `request_id` must stay stable across retries. A changed payload using the same key is refused; a matching request or fingerprint returns the existing receipt without allocating more reels. Cancelled requests require a new request.
- `receipt_labels(name)` returns price-free label data and locally generated QR data URIs. Each QR opens the authenticated ERPNext Batch record. Draft labels are marked **DRAFT - NOT RECEIVED**; cancelled receipts cannot print active labels.

## First shipment

Supplier invoice SI0075290 / PO PUR-ORD-2026-00232 / source PI ACC-PINV-2026-00678: 18 reels, 23,565 kg. The supplier's gross weights were approved as receiving quantities, pending arrival verification. The preparation JSON stays outside the repository and can be loaded using Compass's **Load prepared packing list** control.

Do not enable batch tracking or create live stock until the operator is ready for the first receipt. The explicit enable action refuses any item with stock history. These four CWB items had no stock-ledger history at inspection.

## Verification and release acceptance

Local checks: 19 reel/quantity tests plus 134 existing receiving, purchasing, rules and PO price-guard tests passed. Native bench integration is still required after deployment; stub tests do not prove migration or ledger behaviour.

After deployment, verify the new fields and `reel_lines` returns conversion factor 1000 on the four tonne lines. Create one 18-row draft, re-read quantities/rates/batches, repeat the same request to verify idempotency, then print the paired labels. Nothing should reach the Stock Ledger while the receipt remains draft. Submit only after physical arrival and the existing human submission review. Confirm 18 batch quantities sum to 23,565 kg and PO per_received updates once.

Check label references again after submission, as ERPNext may store the batch through a Serial and Batch Bundle. Keep supplier-declared quantities visibly distinct from measured weights.
