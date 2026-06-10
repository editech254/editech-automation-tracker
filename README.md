# EDITECH Automation Tracker

Streamlit + SQLite app for tracking Kilimall orders. Deployable on Coolify via Docker Compose.

## Local run

```bash
cd streamlit-app
docker compose up --build
```

Then open http://localhost:8501

## Coolify deployment

1. In Coolify, create a new **Docker Compose** resource.
2. Point it to this repo (or paste the `docker-compose.yml`).
3. Coolify will build the image from the `Dockerfile`.
4. Expose port `8501` (Coolify will provide a public URL + SSL).
5. The named volume `editech_data` persists the SQLite DB at `/data/kilimall_automation.db` across redeploys.

## Features

- Upload Kilimall **CSV / Excel** exports (multiple files at once).
- Auto-detects common column names (order_no, shop, product, qty, amount, status, etc.).
- Cleans order numbers (strips non-digits) and de-duplicates by `order_no`.
- View master orders table with CSV / Excel download.
- Stats dashboard: totals, orders per shop, amount per status.
- Danger zone: wipe all records.

## Environment variables

| Var      | Default  | Purpose                                  |
|----------|----------|------------------------------------------|
| `DB_DIR` | `/data`  | Directory where `kilimall_automation.db` is stored. Mount a volume here. |
