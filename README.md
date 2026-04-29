# VCL Procurement

ERPNext custom app for the procurement domain at Vimit Converters Ltd.

## Phase 1: QBO Bill Sync (ERPNext Purchase Invoice → QuickBooks Online)

One-directional weekly push of submitted Purchase Invoices into QuickBooks Online as `Bill` objects, with a human approval queue inside ERPNext and drift detection on QBO-side edits.

### Doctypes (Module: QBO Sync)

**Reference cache** (refreshed weekly by external runner from QBO; read-only for users)

- `QBO Vendor`
- `QBO Account`
- `QBO Item`
- `QBO Tax Code`

**Mappings** (user-editable; `approved=1` activates a mapping)

- `QBO Vendor Map` — Supplier → QBO Vendor
- `QBO Item Map` — Item → QBO Item
- `QBO Account Map` — Account → QBO Account
- `QBO Purchase Tax Map` — (template + account_head + rate) → QBO Tax Code

**Operational**

- `QBO Bill Push Queue` — staged PI → QBO payload, category, approval, push result
- `QBO Drift Log` — QBO-side edits detected after push

### Custom Fields on Purchase Invoice

- `custom_qbo_bill_id` (Data)
- `custom_qbo_synced_at` (Datetime)
- `custom_qbo_sync_status` (Select: Not Synced / Pushed / Drift)

### Per-line routing rule

For each PI line, the runner picks one of two QBO Bill detail types:

```
if line.item_code is set AND QBO Item Map[item_code] is approved:
    → ItemBasedExpenseLineDetail with that ItemRef
elif line.expense_account is set AND QBO Account Map[expense_account] is approved:
    → AccountBasedExpenseLineDetail with that AccountRef
else:
    → BLOCK with reason "line N: no approved item or account mapping"
```

A single QBO Bill payload may mix Item-based and Account-based lines.

### External runner

Lives outside this app at `/opt/vcl/CommandCentre/projects/purchase_qbo_sync/`. Owns: cron, QBO OAuth, QBO API calls, calling the whitelisted endpoints in this app for bulk master upserts, queue staging, and writeback.

## Install

On Frappe Cloud bench:

```bash
bench get-app https://github.com/tjharia93/vcl_procurement
bench --site <site> install-app vcl_procurement
```

## Roles

- **System Manager** — full access to all 10 doctypes
- **Accounts Manager** — read all reference doctypes; read+write+approve maps and queue; review drift
- **Accounts User** — read-only across all 10 doctypes

## License

MIT
