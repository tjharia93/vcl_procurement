# Standard reel identity label: 100 x 70 mm

The reviewed design is now shared by Compass and native ERPNext printing. One
identity label is printed for each existing reel, with its full permanent ID,
material, item code, width, known GSM, supplier and QR. Local labels show the PO;
imports retain their BL and container references. Quantity and receiving fields
are omitted, as are movement logs. Unknown GSM is not invented.

The format is independent of stock UOM: Kg, Tonne and Square Meter sources all
print the same identity design. Neither printing nor migration changes quantities,
unit conversions, IDs, Item masters, Batches, arrival records or stock vouchers.
QR destinations remain `/compass/reels/<existing-ID>`. Existing-stock custody
labels retain their original `/compass/stock-tags/<existing-ID>` destinations.
Existing reels use this same layout with an Existing stock source reference;
no PO number or supplier is invented for an old reel without purchasing records.

Compass requests the shared authorized server renderer instead of maintaining a
second HTML template. Source document permissions are checked again; selected
IDs must belong to that source. This also preserves container/material filters
and single-record reprints. The default stock is 100 x 70 mm. Optional A4 prints
eight identity labels per page. Receiving-list printing remains separate.

Deploy main of **vcl_procurement and vcl_compass together**, then run site migrate.
The `reel_labels_100x70` patch installs these opt-in formats:

- VCL Reel Labels - 100x70 (Purchase Invoice)
- VCL PO Reel Labels - 100x70 (Purchase Order)
- VCL Receipt Reel Labels - 100x70 (Purchase Receipt)
- VCL Reel Label - 100x70 (Reel Intake)
- VCL Batch Reel Label - 100x70 (Batch)
- A4 (8 labels) variants for invoice, PO and receipt

Older app-owned 100x100 and A4 paired-label formats are disabled. Their rendering
entry points also use the new layout for old bookmarks. Custom receiving sheets,
the current PO-specific preview, and ream/bundle/container labels are untouched.
Existing document default formats are not changed. Print Actual size / 100%,
set the printer stock to 100 x 70 mm and disable headers/footers.

PO printing uses already prepared Reel Intake identities. It does not create a
new set. Batch-only printing additionally checks its stock source (or linked PO)
and Item permissions; cancelled sources are refused. The PO-only receiving/posting
workflow remains a later step.
