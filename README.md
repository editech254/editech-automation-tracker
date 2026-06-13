# Kilimall Reconciliation Suite — EDITECH DIGITAL

State-driven Streamlit + PostgreSQL ERP for the full Kilimall order
lifecycle: daily order capture → weekly settlement reconciliation →
exception handling → lifetime BI metrics.

## Modules

| Module | Purpose |
|--------|---------|
| **Auth Gate** | First-launch admin wizard, bcrypt-hashed logins, RBAC. |
| **Dashboard** | Live KPIs (AR, net paid, fees) and audit log. |
| **Receivables** | Editable daily ledger + Excel/CSV bulk upload. |
| **Clearing** | Drop the weekly Kilimall settlement `.xlsx`; auto-matches `bill details`, aggregates `ds processing fee` / `fine` / `Other Deductions`, computes `net_payout`, and archives or routes to suspense. Includes **Rematch Buffer** for late-keyed orders. |
| **Config** | Admin-only: provision users, view shop keyword mapping. |

## Security

- **No credentials in source.** On first launch, the app shows a setup
  wizard that creates the primary admin. Until that wizard runs, the
  application is locked.
- **Passwords are bcrypt-hashed** (per-user salt, work factor 12). The
  legacy SHA-256 path is gone.
- **Password policy:** ≥12 chars, mixed case, digit, symbol.
- **Generic auth errors** (no enumeration via "user not found").
- **Failed logins are audited.**
- **Parameterised SQL** throughout — no string interpolation of the
  `shop` filter.

If you are upgrading from a previous version that seeded
`admin / Admin@Editech2026` and `accountant / Finance@2026`, delete those
rows before deploying:

```sql
DELETE FROM system_users WHERE username IN ('admin', 'accountant');
```

The setup wizard will re-trigger on next launch.

## Database

PostgreSQL. Set `DATABASE_URL`, e.g.:

```
postgresql://user:pass@host:5432/editech_db
```

Tables: `system_users`, `system_audit_logs`, `registered_shops`,
`shop_keywords`, `active_daily_orders`, `unkeyed_buffer`,
`historical_archive`.

## Local run

```bash
docker compose up --build
```

Open <http://localhost:8501> — you'll be prompted to create the first
administrator.

## Expected Kilimall settlement workbook

Sheet names (case-insensitive):

- **`bill details`** — `order_sn`, `complete amount`, `Commission`
- **`ds processing fee`** — `order_no`, `amout`
- **`fine`** — `order_sn`, `fine(KSH)`
- **`Other Deductions`** — `Order SN`, `Amount（ksh）`

Optional sheets generate a toast warning rather than crashing.

## Net payout formula

```
net_payout = complete_amount
           - |commission|
           - |ds_processing_fee|
           - |fines|
           - |other_deductions|
```
