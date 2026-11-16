# Reel preparation from the Purchase Receipt

Development note, 16 November 2026. The receipt should own the receiving workflow;
staff should not need to visit the PO, a stock-registration page or ERPNext Desk
to complete an ordinary paper-reel delivery.

## Current Chentai delivery

- Draft receipt: MAT-PRE-2026-00165; PO PUR-ORD-2026-00279.
- Delivery note 0005972; actual arrival 16 November 2026.
- Both receipt rows have been saved to WAREHOUSE 3 - VCL.
- 1525 mm / 125 GSM: 3 physical reels, combined 3,325 kg.
- 1200 mm / 150 GSM: 4 physical reels, combined 3,569 kg.
- Total: 7 reels / 6,894 kg. Individual weights and supplier reel numbers have
  not been supplied. Never divide these totals between the reels.
- The 1200 mm PO quantity increase was separately approved. The receipt remains
  a draft. The 1525 mm PO line retains 175 kg outstanding after this receipt.

## Minimal preparation being delivered

The purchasing dev receipt page gets a **Prepare reel labels** section.

1. Read saved receipt date, delivery note, PO links and row warehouses.
2. Enter the actual reel count for each relevant receipt row. Show width and GSM
   from explicit receipt text, including legacy `Reel Width: 1525` / `GSM: 125`
   notation. Staff confirm or correct these values before preparation.
3. Review a confirmation: 3 + 4 reels, the two combined kg quantities, date,
   delivery note and warehouses. No ID is reserved merely by opening the page.
4. Explicitly confirm arrival/count/specifications and register the seven tags.
   Each **Reel Intake** stores its permanent ID, exact PO child row, receipt,
   supplier, company, width, GSM, physical warehouse, arrival date and recording
   user/time. Unknown weights, supplier numbers, invoice and Batch remain blank.
5. Show all seven real registered IDs and open the shared **100 x 70 mm** print
   preview. Reprints reuse the same IDs and stable `/compass/reels/<ID>` URL.
6. Keep receipt submission separate. Preparing/printing tags does not change
   ordered/received quantities, rates, taxes, Item tracking or the stock ledger.

This delivery uses unbatched Items. A tag is a physical identity, not an ERPNext
Batch balance. The existing 1525 mm Item has stock history: enabling Batch
tracking directly is not a valid migration path. Do not describe these tags as
complete individual reel stock accounting.

## Full receipt-screen workflow to add next

| Stage | Receipt-screen behaviour | Guard |
| --- | --- | --- |
| Delivery header | Edit actual date, delivery note, department and warehouse; optionally apply warehouse to all selected rows | Active leaf warehouse in the receipt company; save explicitly |
| Reel allocation | Use actual counts for this receipt; show prepared PO/invoice identities and assign them before offering new IDs | Reuse existing tags; preserve exact source row even when item codes repeat |
| Individual entry | Expand 3 and 4 rows with supplier reel number, stated net/gross weight, measured gross, core, net, condition and hold notes | Blank means unknown; never derive an individual weight from a combined total |
| Reconciliation | Show captured total, receipt kg, missing weights and difference per source row | Explain whether the accepted receipt quantity is a supplier declaration or warehouse measurement; explicit discrepancy review |
| Labels | Preview and print all or selected registered IDs, 100 x 70 mm; offer reprint of an existing tag | One ID per physical reel; no reallocation during printing |
| Final receipt review | Show counts, kg by size, warehouses, date, note, held material and excess approval | Separate explicit Submit; ERPNext permissions and validations remain authoritative |
| After submission | Open physical reel, receipt and stock references from one screen | Correct stock through native reviewed vouchers, not edits to original balances |

For tracked/new Items, retain the existing one-Batch-per-reel receipt flow. For
legacy unbatched Items, design and review a reconciliation/migration workflow
before exposing per-reel available kg, issue or return actions. Physical tag
registration alone must not invent those balances.

## Concurrency and correction rules

- Lock source POs before the receipt and reserve IDs with the shared Series
  allocator. Preparation and its records commit in one POST transaction.
- Review binds to the saved receipt version. Refuse a changed source before
  first registration. A lost-response retry reuses the matching identity set.
- Refuse another set when unassigned PO/invoice tags already exist for the
  selected PO row. A future assignment screen must reuse those identities.
- Store a receipt-source fingerprint with each tag. Changed quantities, date,
  warehouse, delivery note, source/item/batch links must trigger reviewed
  correction before reprinting. Do not silently move already printed tags.
- Keep supplier invoice/BL/container blank when not present on an authoritative
  source. A delivery-note number is not an invoice number.
- Cancellation, returns, cross-company sources, invalid counts, unauthorized
  access and future physical-arrival dates are rejected.

## Deployment and verification

Backend: `vcl_procurement.receipt_reel_tags`; no schema changes or new DocTypes.
Frontend: shared `ReceiptReelTags` section on the purchasing dev receipt page.
Deploy the procurement commit, verify read-only `page`, then publish the tested
purchasing dev Web Page bundle. The full installed Compass frontend remains a
separate app deployment. Test duplicate/retry/stale-source behaviour, source
permissions, desktop/phone layouts, QR destination and exact print dimensions.
