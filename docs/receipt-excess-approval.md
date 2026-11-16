# Excess delivery confirmation

Purchasing receiving previews quantities through `api.receipt_excess.preview`.
Quantities above the configured receipt ceiling can be proposed as explicit PO
quantity increases. This does not change Item or Stock Settings allowances.

`api.receipt_excess.approve` is POST-only and requires Purchase Manager or System
Manager, parent read/write permission, an open submitted PO and its reviewed
modified version. It locks the PO, validates the requested quantities, and passes
every original child row to native ERPNext Update Items, with only affected
quantities increased. Rates, descriptions, dates, UOMs and unchanged rows are
preserved. Native workflow, authorization, budget, tax and payment checks run.

Approval and its audit comment commit in one request. An approval ID plus the
request fingerprint makes a lost response safely retryable. Reusing the ID for
changed data, stale PO versions or insufficient permission is refused. The server
never creates or submits a Purchase Receipt in this action. The receiving page
retains typed quantities and requires a separate receipt action after approval.

For the reviewed Chentai delivery, line 2 is proposed from 2,000 to 3,569 kg at
KES 83/kg: 1,569 kg and KES 130,227 additional value before tax. Line 1 remains
ordered at 3,500 kg while the entered delivery quantity remains 3,325 kg. These
are a proposal until the user explicitly approves on screen.

Deploy procurement Python code before publishing the updated purchasing Web
Page. No DocType, Custom Field, patch or schema migration is added. The existing
receipt submission and individual-reel tracking rules remain authoritative.
