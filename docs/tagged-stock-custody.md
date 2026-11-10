# Existing stock and physical custody

Release: 10 November 2026. Deploy `vcl_procurement` and run migrate before the matching Compass build. No production registration, arrival or movement is created by installation.

Stores now offers **Register existing reels / reams** and **Move tagged stock**. This is physical custody evidence, not an ERPNext stock ledger. No PO, invoice, Item, Batch, Stock Reconciliation, Stock Entry or valuation is created or changed.

## Operator flow

1. Inspect the existing stock at its actual warehouse, including any old VCL labels. Group matching specifications and packing. Register tagged and untagged units separately.
2. Select company and actual source. Enter material/specification, physical count and date. Bundles require sheets per ream and reams per wrapper; a single wrapped ream is one bundle containing one ream. Unknown reel weight stays blank; weighed reels must be registered individually.
3. Enter existing VCL IDs one per line to reuse them. Leave the field blank only when no VCL identity exists. Confirm the physical inspection. Reopening the same registration reference with the same canonical plan returns the original identities.
4. Print labels: reel identity and log at 100 × 100 mm; bundles at 100 × 30 mm. Attach and scan. New labels open `/compass/stock-tags/<ID>`. Existing reel/bundle pages link to custody history, so existing Compass labels remain usable.
5. Select available tags at the source, destination, dispatch date and custodian. Review then confirm physical dispatch. Print the A4 checklist. The unit leaves the source custody list and becomes In transit.
6. Check each unit at the destination. Received establishes destination custody; bundle counts must match. On hold establishes destination custody but blocks onward dispatch. Missing remains in transit and can be checked again. Returned means physically back at the source and is held pending inspection.
7. Release a held unit only after an inspection note/date and matching bundle count. A hold on its original registration/intake must also be resolved there. Retain the permanent ID throughout.

## Controls and boundaries

- Stock Manager or System Manager role, Stores module access, and native company/document read permissions are required. DocTypes expose read/report/print only; service methods own writes.
- Four new DocTypes: Stock Tag Registration, Physical Stock Unit, Tagged Stock Movement and its Tagged Movement Unit child table. `track_changes` preserves changes and prior exception checks; physical event dates and server actor/timestamps are separate.
- Company locks serialize new registration and dispatch requests. Series locks use the existing reel/bundle allocators. Imported high IDs advance the same Series; reel allocation also checks the custody registry. Repeated registration/dispatch requests are idempotent.
- Movement and sorted unit row locks plus modified-version checks prevent duplicate dispatch or stale receipt. Exceptions cannot silently change a registered bundle's contents. Completed checks cannot be rewritten through the receipt endpoint.
- Existing Reel Intake IDs require recorded arrival at the same warehouse. Existing material dimensions and bundle packing/colour/material must match. Existing Batch-only IDs require a readable source voucher to establish company. Unrecognized legacy VCL IDs can be imported by an authorized manager after physical inspection.
- Existing IDs are independent of Item mapping. Future stock posting must establish reviewed item/UOM/quantity/valuation at the actual source and reconcile existing ledger balances; never infer stock from the custody register or create a second identity.
- Whole reels/wrapped bundles only. No repacking/splitting, partial reel consumption, IC movements, automated stock posting or accounting transfer in this release. Reel weight is explicitly the registration observation, not a current balance.
- Browse returns up to 200 matching units, 50 registrations and 100 movements; full-ID search finds older units. No prices are exposed.

## Validation and deployment

399 backend tests across 24 isolated modules passed, including 16 new custody tests. They cover identity reuse, retries, source scope, stale/duplicate dispatch, missing/found, returned/held units, counts and label destinations. These are Frappe-stub tests, not a live bench integration test.

Matching Compass: 546 frontend tests, TypeScript checks, production build, and mocked desktop/390px browser flow through registration → label print → dispatch → receipt/missing/hold → release. Authentication redirect tests cover new routes.

After deployment/migrate, check new DocType schemas and GET `vcl_procurement.tagged_stock.options` / `browse` with a Stock Manager account. Open Stores and confirm the two buttons. Use actual checked material for the first operational registration; do not generate dummy permanent IDs on production. No stock posting should result.
