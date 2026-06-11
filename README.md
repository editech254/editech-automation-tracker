# Kilimall Reconciliation Suite — EDITECH DIGITAL

A state-driven Streamlit + SQLite application for managing the complete
Kilimall order lifecycle: daily order capture, weekly multi-sheet
settlement reconciliation, exception handling, and lifetime BI metrics.

## Modules

| Module | Purpose |
|--------|---------|
| **A — Scorecard** | Live KPIs: Total Sold, Net Paid, Fees, Pending. |
| **B — Daily Ledger** | Editable grid (`st.data_editor`) for daily dispatched orders. Paste from Excel, add/delete rows, commit. |
| **C — Reconciliation Engine** | Drop the weekly multi-sheet Kilimall settlement `.xlsx`. Auto-cleans order IDs (strips quotes/commas), aggregates `bill details`, `ds processing fee`, `fine`, `Other Deductions`, computes `net_payout`, and archives matched orders. |
| **D — Un-keyed Buffer** | Orders found on Kilimall settlement but missing from daily ledger. Key them in, then click **Rematch Buffer**. |
| **E — Lifetime Archive** | All settled orders with CSV/Excel export. |

## Database

SQLite at `/app/data/reconciliation.db` (override with `DB_DIR` env var).

Three persistent tables: `active_daily_orders`, `unkeyed_buffer`, `historical_archive`.

## Local run

```bash
cd streamlit-app
docker compose up --build
```

Open <http://localhost:8501>.

## Coolify deployment

1. Push this folder to a Git repository (GitHub/GitLab).
2. In Coolify → **New Resource → Docker Compose**.
3. Connect your repo and point the build context to the folder containing `docker-compose.yml`.
4. Coolify auto-builds from `Dockerfile`. Expose port `8501` — Coolify provides a public URL and SSL.
5. The named volume `kilimall_data` persists the SQLite DB across redeploys.

### Environment variables

| Var | Default | Purpose |
|-----|---------|---------|
| `DB_DIR` | `/app/data` | Where `reconciliation.db` lives. Must be a mounted volume. |

## Expected Kilimall settlement workbook

Sheet names (case-insensitive, spaces flexible):

- **`bill details`** — `order_sn`, `complete amount`, `Commission`, `settlement`
- **`ds processing fee`** — `order_no`, `amout`
- **`fine`** — `order_sn`, `fine(KSH)`
- **`Other Deductions`** — `Order SN`, `Amount（ksh）`

Missing optional sheets/columns generate a `st.toast` warning instead of crashing.

## Net payout formula

```
net_payout = complete_amount
           - |commission|
           - |ds_processing_fee|
           - |fines|
           - |other_deductions|
```
