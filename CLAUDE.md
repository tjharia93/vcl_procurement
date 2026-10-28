# VCL Procurement — start here

A Frappe app that holds VCL's purchasing logic on ERPNext. It does two jobs. First, it stages
submitted Purchase Invoices into a QuickBooks Online Bill push queue, with approval and drift
detection (module QBO Sync). Second, it is the server side of the purchasing screens: purchase
orders, receipts, purchase invoices, material requests and the KRA / ACD submit gates. The
screens live in `vcl_compass` and on the `/purchasing-dev` dev page; both call this app.

**Read `MEMORY.md` next**: current state, decisions and traps.

## Where it runs
- Site: https://vimitconverters.frappe.cloud
- Deploys from branch: `main`. Pushing to main does NOT deploy. Tanuj deploys on Frappe Cloud,
  and runs a migrate when a DocType or patch changes.
- No CI workflow in the repo.
- Callers: Compass screens and the VCL phone app (`vcl_procurement.api.*`), the external QBO
  runner at `/opt/vcl/CommandCentre/projects/purchase_qbo_sync/` (masters, mappings, push queue),
  and Desk through `doc_events` in `hooks.py`.

## Working rules
- Commit as `-c user.name=Claude -c user.email=noreply@anthropic.com`.
- Never put an API key, token or password in the repo or in chat.
- New custom fields go through `setup_fields.py` (patch + `after_install`), not by hand.
- Keep business rules in pure modules (`api/rules.py`, `mr_rules.py`, `notify_rules.py`,
  `landing_rules.py`) and test them there.
- Tests run without a bench (`frappe` is stubbed). From the repo root:
  `python3 -m unittest vcl_procurement.tests.test_rules` (and each other `test_*` module in
  `vcl_procurement/tests/` and `vcl_procurement/api/`). All passed on 28/10/2026.
- After deploy + migrate: `python3 scripts/smoke_purchasing.py` (read-only, live site).

## Map
| Path | What lives there |
|---|---|
| `vcl_procurement/hooks.py` | PI before_submit gates, PI submit/cancel staging, PO price guard, crons |
| `vcl_procurement/api/` | whitelisted methods: `po`, `pi`, `purchasing`, `raise_po`, `mr`, `pr`, `search`, `acd`, `push_watch`, `staging`, `masters`, `mappings`, `push_queue` |
| `vcl_procurement/api/rules.py` | dates, KES, payment schedule, gates, units, tax rows (pure Python) |
| `vcl_procurement/qbo_sync/doctype/` | 11 QBO Sync DocTypes (caches, maps, push queue, drift log, settings) |
| `vcl_procurement/reels.py` | receive paper reels one Batch at a time |
| `vcl_procurement/setup_fields.py`, `install.py` | idempotent custom fields, after_install |
| `vcl_procurement/patches/`, `patches.txt` | migrate patches (all post_model_sync) |
| `vcl_procurement/tests/` | unit tests, no bench needed |
| `scripts/smoke_purchasing.py` | read-only post-deploy check |
| `PURCHASING_PORT.md` | the dev-page port: decisions and API contract changes by date |
