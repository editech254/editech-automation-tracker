import streamlit as st
import pandas as pd
import sqlite3
import os
import hashlib
import io
from datetime import datetime, timedelta

# ---------------- Page Configuration ----------------
st.set_page_config(
    page_title="EDITECH Automation Tracker",
    layout="wide",
    page_icon="🚀",
)

# ---------------- Database Setup ----------------
DB_DIR = os.environ.get("DB_DIR", "/data")
os.makedirs(DB_DIR, exist_ok=True)
DB_FILE = os.path.join(DB_DIR, "editech.db")


def get_conn():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS master_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_no TEXT,
            shop_name TEXT,
            product TEXT,
            quantity INTEGER,
            amount REAL,
            status TEXT,
            source TEXT,
            file_name TEXT,
            created_at TEXT,
            cost REAL DEFAULT 0,
            tags TEXT,
            notes TEXT
        )
        """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS upload_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_name TEXT,
            file_hash TEXT,
            rows_inserted INTEGER,
            rows_updated INTEGER,
            uploaded_at TEXT
        )
        """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT,
            details TEXT,
            rows_affected INTEGER,
            performed_at TEXT
        )
        """
    )

    # Migration: add cost, tags, notes if missing
    for col, dtype in [("cost", "REAL DEFAULT 0"), ("tags", "TEXT"), ("notes", "TEXT")]:
        try:
            c.execute(f"ALTER TABLE master_orders ADD COLUMN {col} {dtype}")
        except sqlite3.OperationalError:
            pass

    # Indexes for performance
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_orders_shop ON master_orders(shop_name)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_orders_status ON master_orders(status)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_orders_source ON master_orders(source)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_orders_created ON master_orders(created_at)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_orders_order_no ON master_orders(order_no)"
    )

    conn.commit()
    conn.close()


def run_select(query, params=()):
    conn = get_conn()
    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return df


def run_delete(where_sql, params=()):
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"DELETE FROM master_orders WHERE {where_sql}", params)
    rows = c.rowcount
    conn.commit()
    conn.close()
    return rows


def run_sql(query, params=()):
    conn = get_conn()
    try:
        df = pd.read_sql_query(query, conn, params=params)
    except Exception as e:
        df = pd.DataFrame({"error": [str(e)]})
    conn.close()
    return df


def file_hash(file_bytes):
    return hashlib.md5(file_bytes).hexdigest()


def log_audit(action, details, rows_affected=0):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO audit_log (action, details, rows_affected, performed_at) VALUES (?, ?, ?, ?)",
        (action, details, rows_affected, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def distinct_values(column):
    df = run_select(f"SELECT DISTINCT {column} FROM master_orders WHERE {column} IS NOT NULL ORDER BY {column}")
    return df[column].tolist() if not df.empty else []


init_db()

# ---------------- Sidebar ----------------
st.sidebar.title("⚙️ Operations")

# DB size
db_size = os.path.getsize(DB_FILE)
st.sidebar.metric("DB Size", f"{db_size / 1024:.1f} KB")

total_rows = run_select("SELECT COUNT(*) as c FROM master_orders")["c"].iloc[0]
st.sidebar.metric("Total Orders", int(total_rows))

# Backup
if st.sidebar.button("💾 Backup DB"):
    with open(DB_FILE, "rb") as f:
        st.sidebar.download_button(
            "Download Backup",
            data=f.read(),
            file_name=f"editech_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db",
            mime="application/octet-stream",
        )

# Restore
restore_file = st.sidebar.file_uploader("🔁 Restore DB", type=["db"])
if restore_file:
    if st.sidebar.button("Confirm Restore"):
        with open(DB_FILE, "wb") as f:
            f.write(restore_file.read())
        st.sidebar.success("Database restored. Refresh the page.")
        st.stop()

if st.sidebar.button("🔄 Refresh Cache"):
    st.cache_data.clear()
    st.rerun()

# Recent audit
st.sidebar.subheader("Recent Activity")
audit_df = run_select(
    "SELECT action, rows_affected, performed_at FROM audit_log ORDER BY performed_at DESC LIMIT 5"
)
if not audit_df.empty:
    st.sidebar.dataframe(audit_df, hide_index=True, use_container_width=True)

# ---------------- Main Tabs ----------------
tabs = st.tabs(
    [
        "📤 Upload",
        "📊 Dashboard",
        "📋 Orders",
        "🔍 Search",
        "📈 Analytics",
        "💰 Profitability",
        "🗑️ Delete / Archive",
        "🧪 SQL Console",
    ]
)

# ---------- Tab 0: Upload ----------
with tabs[0]:
    st.header("📤 Upload Data")

    logged_via = st.selectbox(
        "Log source as",
        ["Settlement Report", "Test Data", "Manual Entry", "Shopify Export", "Other"],
    )
    update_existing = st.checkbox("Update existing orders on conflict (match by order_no + shop_name)")

    uploaded = st.file_uploader(
        "Drop CSV/Excel files",
        type=["csv", "xlsx"],
        accept_multiple_files=True,
    )

    if uploaded:
        for f in uploaded:
            bytes_data = f.read()
            fhash = file_hash(bytes_data)

            # Deduplication check
            dup = run_select(
                "SELECT * FROM upload_log WHERE file_hash = ?", (fhash,)
            )
            if not dup.empty and not update_existing:
                st.warning(f"⏭️ Skipped {f.name} — already uploaded.")
                continue

            if f.name.endswith(".csv"):
                df = pd.read_csv(io.BytesIO(bytes_data))
            else:
                df = pd.read_excel(io.BytesIO(bytes_data))

            # Normalize columns
            df.columns = [c.lower().strip().replace(" ", "_") for c in df.columns]

            # Map common columns
            col_map = {
                "order_no": ["order_no", "order_number", "order id", "order_id", "id"],
                "shop_name": ["shop_name", "shop", "store", "store_name"],
                "product": ["product", "product_name", "item", "title"],
                "quantity": ["quantity", "qty", "units"],
                "amount": ["amount", "total", "price", "revenue", "gmv"],
                "status": ["status", "order_status", "state"],
                "cost": ["cost", "cogs", "product_cost"],
            }

            def find_col(options):
                for opt in options:
                    if opt in df.columns:
                        return opt
                return None

            mapped = {k: find_col(v) for k, v in col_map.items()}

            rows_inserted = 0
            rows_updated = 0

            conn = get_conn()
            c = conn.cursor()

            for _, row in df.iterrows():
                order_no = str(row.get(mapped["order_no"], ""))
                shop = str(row.get(mapped["shop_name"], ""))
                product = str(row.get(mapped["product"], ""))
                qty = pd.to_numeric(row.get(mapped["quantity"], 0), errors="coerce") or 0
                amount = pd.to_numeric(row.get(mapped["amount"], 0), errors="coerce") or 0
                status = str(row.get(mapped["status"], "pending"))
                cost = pd.to_numeric(row.get(mapped["cost"], 0), errors="coerce") or 0

                if not order_no or not shop:
                    continue

                if update_existing:
                    c.execute(
                        """
                        SELECT id FROM master_orders WHERE order_no = ? AND shop_name = ?
                        """,
                        (order_no, shop),
                    )
                    existing = c.fetchone()
                    if existing:
                        c.execute(
                            """
                            UPDATE master_orders
                            SET product = ?, quantity = ?, amount = ?, status = ?, cost = ?, source = ?, file_name = ?, created_at = ?
                            WHERE id = ?
                            """,
                            (
                                product,
                                int(qty),
                                float(amount),
                                status,
                                float(cost),
                                logged_via,
                                f.name,
                                datetime.now().isoformat(),
                                existing["id"],
                            ),
                        )
                        rows_updated += 1
                        continue

                c.execute(
                    """
                    INSERT INTO master_orders (order_no, shop_name, product, quantity, amount, status, source, file_name, created_at, cost)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        order_no,
                        shop,
                        product,
                        int(qty),
                        float(amount),
                        status,
                        logged_via,
                        f.name,
                        datetime.now().isoformat(),
                        float(cost),
                    ),
                )
                rows_inserted += 1

            c.execute(
                """
                INSERT INTO upload_log (file_name, file_hash, rows_inserted, rows_updated, uploaded_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (f.name, fhash, rows_inserted, rows_updated, datetime.now().isoformat()),
            )

            conn.commit()
            conn.close()

            log_audit("UPLOAD", f"{f.name} via {logged_via}", rows_inserted + rows_updated)
            st.success(
                f"✅ {f.name}: inserted {rows_inserted}, updated {rows_updated}"
            )

# ---------- Tab 1: Dashboard ----------
with tabs[1]:
    st.header("📊 Dashboard")

    df = run_select("SELECT * FROM master_orders")
    if df.empty:
        st.info("No data yet. Upload some orders!")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Orders", len(df))
        c2.metric("Total GMV", f"${df['amount'].sum():,.2f}")
        c3.metric("Total Quantity", int(df["quantity"].sum()))
        c4.metric("Unique Shops", df["shop_name"].nunique())

        st.subheader("Orders by Shop")
        st.bar_chart(df.groupby("shop_name")["order_no"].count())

        st.subheader("Amount by Status")
        st.bar_chart(df.groupby("status")["amount"].sum())

# ---------- Tab 2: Orders ----------
with tabs[2]:
    st.header("📋 All Orders")

    page_size = st.selectbox("Page size", [25, 50, 100, 250, 500], index=1)
    total = run_select("SELECT COUNT(*) as c FROM master_orders")["c"].iloc[0]
    pages = max(1, (total + page_size - 1) // page_size)
    page = st.number_input("Page", min_value=1, max_value=pages, value=1, step=1)

    offset = (page - 1) * page_size
    df = run_select(
        "SELECT * FROM master_orders ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (page_size, offset),
    )

    if df.empty:
        st.info("No orders found.")
    else:
        st.dataframe(df, hide_index=True, use_container_width=True)
        st.caption(f"Showing {len(df)} of {total} orders — Page {page} of {pages}")

# ---------- Tab 3: Search ----------
with tabs[3]:
    st.header("🔍 Search & Filter")

    q = st.text_input("Search text (order_no, product, shop)")
    shops = st.multiselect("Shop", distinct_values("shop_name"))
    statuses = st.multiselect("Status", distinct_values("status"))
    sources = st.multiselect("Source", distinct_values("source"))

    c1, c2 = st.columns(2)
    date_from = c1.date_input("From", value=None)
    date_to = c2.date_input("To", value=None)
    min_amount = st.number_input("Min Amount", value=0.0, step=10.0)

    if st.button("Run Search"):
        clauses = ["1=1"]
        params = []

        if q:
            clauses.append(
                "(order_no LIKE ? OR product LIKE ? OR shop_name LIKE ?)"
            )
            params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
        if shops:
            placeholders = ",".join(["?"] * len(shops))
            clauses.append(f"shop_name IN ({placeholders})")
            params.extend(shops)
        if statuses:
            placeholders = ",".join(["?"] * len(statuses))
            clauses.append(f"status IN ({placeholders})")
            params.extend(statuses)
        if sources:
            placeholders = ",".join(["?"] * len(sources))
            clauses.append(f"source IN ({placeholders})")
            params.extend(sources)
        if date_from:
            clauses.append("created_at >= ?")
            params.append(date_from.isoformat())
        if date_to:
            clauses.append("created_at <= ?")
            params.append(date_to.isoformat())
        if min_amount > 0:
            clauses.append("amount >= ?")
            params.append(min_amount)

        where = " AND ".join(clauses)
        df = run_select(f"SELECT * FROM master_orders WHERE {where} ORDER BY created_at DESC", params)

        st.write(f"**{len(df)} results**")
        st.dataframe(df, hide_index=True, use_container_width=True)

        if not df.empty:
            csv = df.to_csv(index=False).encode("utf-8")
            st.download_button("📥 Export CSV", csv, "search_results.csv", "text/csv")

# ---------- Tab 4: Analytics ----------
with tabs[4]:
    st.header("📈 Analytics")

    df = run_select("SELECT * FROM master_orders")
    if df.empty:
        st.info("Upload data to see analytics.")
    else:
        df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce")

        st.subheader("Daily Trend (GMV)")
        daily = df.groupby(df["created_at"].dt.date)["amount"].sum().reset_index()
        daily.columns = ["date", "gmv"]
        st.line_chart(daily.set_index("date"))

        st.subheader("Top 10 Shops by GMV")
        top_shops = df.groupby("shop_name")["amount"].sum().nlargest(10).reset_index()
        st.bar_chart(top_shops.set_index("shop_name"))

        st.subheader("Top 10 Products by Quantity")
        top_products = df.groupby("product")["quantity"].sum().nlargest(10).reset_index()
        st.bar_chart(top_products.set_index("product"))

        st.subheader("Status Breakdown")
        status_df = df.groupby("status").agg({"amount": "sum", "order_no": "count"}).reset_index()
        status_df.columns = ["status", "gmv", "orders"]
        st.dataframe(status_df, hide_index=True, use_container_width=True)

        aov = df["amount"].sum() / max(1, len(df))
        st.metric("Average Order Value (AOV)", f"${aov:,.2f}")

# ---------- Tab 5: Profitability ----------
with tabs[5]:
    st.header("💰 Profitability")

    margin = st.slider("Assumed margin % (used when cost = 0)", 0, 100, 30)

    df = run_select("SELECT * FROM master_orders")
    if df.empty:
        st.info("No data.")
    else:
        df["effective_cost"] = df["cost"].fillna(0)
        zero_cost_mask = df["effective_cost"] == 0
        df.loc[zero_cost_mask, "effective_cost"] = df.loc[zero_cost_mask, "amount"] * (1 - margin / 100)

        df["profit"] = df["amount"] - df["effective_cost"]

        c1, c2, c3 = st.columns(3)
        c1.metric("Revenue", f"${df['amount'].sum():,.2f}")
        c2.metric("Cost", f"${df['effective_cost'].sum():,.2f}")
        c3.metric("Profit", f"${df['profit'].sum():,.2f}")

        st.subheader("Most Profitable Products")
        prof = df.groupby("product")["profit"].sum().nlargest(10).reset_index()
        st.bar_chart(prof.set_index("product"))

        st.subheader("Profit by Shop")
        shop_prof = df.groupby("shop_name")["profit"].sum().reset_index()
        st.bar_chart(shop_prof.set_index("shop_name"))

# ---------- Tab 6: Delete / Archive ----------
with tabs[6]:
    st.header("🗑️ Delete / Archive")
    st.warning("⚠️ Destructive actions are logged and cannot be undone. Always preview first.")

    st.subheader("Guided Filters")
    del_sources = st.multiselect("Source", distinct_values("source"))
    del_shops = st.multiselect("Shop", distinct_values("shop_name"))
    del_statuses = st.multiselect("Status", distinct_values("status"))

    c1, c2 = st.columns(2)
    del_from = c1.date_input("Created from", value=None, key="del_from")
    del_to = c2.date_input("Created to", value=None, key="del_to")

    order_like = st.text_input("Order number contains")

    st.subheader("Custom SQL WHERE (optional — overrides guided filters)")
    custom_where = st.text_area("e.g. source = 'Test Data' AND status = 'cancelled'")

    # Build WHERE clause
    if custom_where.strip():
        where_sql = custom_where.strip()
        preview_params = ()
    else:
        clauses = []
        params = []
        if del_sources:
            clauses.append(f"source IN ({','.join(['?']*len(del_sources))})")
            params.extend(del_sources)
        if del_shops:
            clauses.append(f"shop_name IN ({','.join(['?']*len(del_shops))})")
            params.extend(del_shops)
        if del_statuses:
            clauses.append(f"status IN ({','.join(['?']*len(del_statuses))})")
            params.extend(del_statuses)
        if del_from:
            clauses.append("created_at >= ?")
            params.append(del_from.isoformat())
        if del_to:
            clauses.append("created_at <= ?")
            params.append(del_to.isoformat())
        if order_like:
            clauses.append("order_no LIKE ?")
            params.append(f"%{order_like}%")

        if not clauses:
            st.info("Select filters or enter custom SQL to preview.")
            where_sql = "1=0"
            preview_params = ()
        else:
            where_sql = " AND ".join(clauses)
            preview_params = tuple(params)

    preview = run_select(f"SELECT * FROM master_orders WHERE {where_sql} LIMIT 500", preview_params)
    st.write(f"**Preview: {len(preview)} rows match**")
    st.dataframe(preview, hide_index=True, use_container_width=True)

    if not preview.empty:
        csv = preview.to_csv(index=False).encode("utf-8")
        st.download_button("📥 Download backup CSV before deleting", csv, "backup_before_delete.csv", "text/csv")

    st.divider()
    st.subheader("Confirm Deletion")

    wipe_all = st.checkbox("WIPE ALL DATA (ignores filters)")
    confirm = st.text_input("Type DELETE to confirm, or WIPE ALL if checkbox is ticked")

    if st.button("🚨 Execute Deletion", type="primary"):
        if wipe_all:
            if confirm.strip() == "WIPE ALL":
                conn = get_conn()
                c = conn.cursor()
                c.execute("DELETE FROM master_orders")
                rows = c.rowcount
                conn.commit()
                conn.close()
                log_audit("WIPE_ALL", "Deleted all orders", rows)
                st.success(f"Deleted {rows} rows. Database wiped.")
                st.rerun()
            else:
                st.error("Type WIPE ALL to confirm.")
        else:
            if confirm.strip() == "DELETE":
                rows = run_delete(where_sql, preview_params)
                log_audit("DELETE", f"WHERE {where_sql}", rows)
                st.success(f"Deleted {rows} rows.")
                st.rerun()
            else:
                st.error("Type DELETE to confirm.")

# ---------- Tab 7: SQL Console ----------
with tabs[7]:
    st.header("🧪 SQL Console (Read-Only SELECT)")
    st.info("Only SELECT queries are allowed here. Use the Delete tab for destructive operations.")

    query = st.text_area("SQL Query", "SELECT * FROM master_orders LIMIT 100")
    if st.button("Run Query"):
        q_clean = query.strip().lower()
        if any(bad in q_clean for bad in ["delete", "update", "insert", "drop", "alter", "create", "truncate"]):
            st.error("❌ Only SELECT queries are permitted in the console.")
        else:
            result = run_sql(query)
            st.write(f"**{len(result)} rows**")
            st.dataframe(result, hide_index=True, use_container_width=True)
            if not result.empty and "error" not in result.columns:
                csv = result.to_csv(index=False).encode("utf-8")
                st.download_button("📥 Export", csv, "query_results.csv", "text/csv")

st.caption(f"DB file: {DB_FILE} • Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}")
