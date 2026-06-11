import streamlit as st
import pandas as pd
import sqlite3
import os
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
DB_FILE = os.path.join(DB_DIR, "kilimall_automation.db")


def get_conn():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS master_orders (
            order_no TEXT PRIMARY KEY,
            shop_id TEXT,
            shop_name TEXT,
            product_name TEXT,
            quantity INTEGER,
            amount REAL,
            cost REAL DEFAULT 0,
            order_time TEXT,
            status TEXT,
            logged_via TEXT,
            tags TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    # Migrations for older DBs
    cols = {r[1] for r in c.execute("PRAGMA table_info(master_orders)").fetchall()}
    for col, ddl in [
        ("cost", "ALTER TABLE master_orders ADD COLUMN cost REAL DEFAULT 0"),
        ("tags", "ALTER TABLE master_orders ADD COLUMN tags TEXT"),
        ("notes", "ALTER TABLE master_orders ADD COLUMN notes TEXT"),
    ]:
        if col not in cols:
            try:
                c.execute(ddl)
            except sqlite3.OperationalError:
                pass

    c.execute("CREATE INDEX IF NOT EXISTS idx_shop ON master_orders(shop_name)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_status ON master_orders(status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_source ON master_orders(logged_via)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_time ON master_orders(order_time)")

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT,
            details TEXT,
            rows_affected INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    conn.close()


init_db()


def log_audit(action: str, details: str, rows: int = 0):
    conn = get_conn()
    conn.execute(
        "INSERT INTO audit_log (action, details, rows_affected) VALUES (?, ?, ?)",
        (action, details, rows),
    )
    conn.commit()
    conn.close()


# ---------------- Helpers ----------------
def clean_order_string(series: pd.Series) -> pd.Series:
    return series.astype(str).str.replace(r"[^\d]", "", regex=True).str.strip()


def read_any(file) -> pd.DataFrame:
    name = file.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(file)
    return pd.read_excel(file)


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    return df


COLUMN_ALIASES = {
    "order_no": ["order_no", "order_number", "order_id", "orderno", "order"],
    "shop_id": ["shop_id", "shopid"],
    "shop_name": ["shop_name", "shop", "store_name"],
    "product_name": ["product_name", "product", "item", "item_name"],
    "quantity": ["quantity", "qty"],
    "amount": ["amount", "total", "price", "total_amount"],
    "cost": ["cost", "unit_cost", "cost_price"],
    "order_time": ["order_time", "order_date", "date", "created_at"],
    "status": ["status", "order_status"],
}


def map_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame()
    for target, aliases in COLUMN_ALIASES.items():
        for a in aliases:
            if a in df.columns:
                out[target] = df[a]
                break
        if target not in out.columns:
            out[target] = None
    return out


def upsert_orders(df: pd.DataFrame, logged_via: str, update_on_conflict: bool = False) -> tuple[int, int, int]:
    """Insert new orders. Returns (inserted, updated, skipped)."""
    conn = get_conn()
    c = conn.cursor()
    inserted, updated, skipped = 0, 0, 0
    for _, row in df.iterrows():
        order_no = row.get("order_no")
        if not order_no:
            skipped += 1
            continue
        values = (
            str(order_no),
            str(row.get("shop_id") or ""),
            str(row.get("shop_name") or ""),
            str(row.get("product_name") or ""),
            int(row["quantity"]) if pd.notna(row.get("quantity")) else 0,
            float(row["amount"]) if pd.notna(row.get("amount")) else 0.0,
            float(row["cost"]) if pd.notna(row.get("cost")) else 0.0,
            str(row.get("order_time") or ""),
            str(row.get("status") or ""),
            logged_via,
        )
        try:
            c.execute(
                """
                INSERT INTO master_orders
                (order_no, shop_id, shop_name, product_name, quantity, amount, cost, order_time, status, logged_via)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            inserted += 1
        except sqlite3.IntegrityError:
            if update_on_conflict:
                c.execute(
                    """
                    UPDATE master_orders SET
                      shop_id=?, shop_name=?, product_name=?, quantity=?, amount=?, cost=?,
                      order_time=?, status=?, logged_via=?
                    WHERE order_no=?
                    """,
                    (*values[1:], values[0]),
                )
                updated += 1
            else:
                skipped += 1
    conn.commit()
    conn.close()
    log_audit("upload", f"source={logged_via}", inserted + updated)
    return inserted, updated, skipped


def fetch_all() -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query("SELECT * FROM master_orders ORDER BY created_at DESC", conn)
    conn.close()
    return df


def distinct_values(col: str) -> list:
    conn = get_conn()
    try:
        rows = conn.execute(
            f"SELECT DISTINCT {col} FROM master_orders WHERE {col} IS NOT NULL AND {col} != '' ORDER BY 1"
        ).fetchall()
    except Exception:
        rows = []
    conn.close()
    return [r[0] for r in rows]


def run_select(query: str, params: tuple = ()) -> pd.DataFrame:
    conn = get_conn()
    try:
        return pd.read_sql_query(query, conn, params=params)
    finally:
        conn.close()


def run_safe_select(query: str) -> pd.DataFrame:
    q = query.strip().rstrip(";")
    if not q.lower().startswith("select"):
        return pd.DataFrame({"error": ["Only SELECT statements are allowed."]})
    conn = get_conn()
    try:
        return pd.read_sql_query(q, conn)
    except Exception as e:
        return pd.DataFrame({"error": [str(e)]})
    finally:
        conn.close()


# ---------------- UI ----------------
st.title("📊 EDITECH DIGITAL — Kilimall Automation Software")
st.markdown("Upload your raw Kilimall files. Reconcile, analyze, export & manage data safely.")

# Sidebar — Ops
with st.sidebar:
    st.header("⚙️ Ops")
    try:
        size_mb = os.path.getsize(DB_FILE) / (1024 * 1024)
        st.metric("DB size", f"{size_mb:.2f} MB")
    except OSError:
        st.metric("DB size", "n/a")
    total_rows = run_select("SELECT COUNT(*) AS n FROM master_orders")["n"].iloc[0]
    st.metric("Total rows", int(total_rows))

    st.divider()
    st.caption("Backup / Restore")
    try:
        with open(DB_FILE, "rb") as f:
            st.download_button("⬇️ Backup DB", f.read(), "kilimall_automation.db", "application/octet-stream")
    except OSError:
        pass
    restore = st.file_uploader("Restore DB (.db)", type=["db"], key="restore_db")
    if restore and st.button("Replace DB", key="do_restore"):
        with open(DB_FILE, "wb") as f:
            f.write(restore.read())
        log_audit("restore", restore.name, 0)
        st.success("Database restored. Reload the page.")

tabs = st.tabs([
    "📤 Upload", "📋 Master Orders", "🔎 Search",
    "📈 Stats", "💰 Profitability", "🗑️ Delete", "🛠️ SQL Console",
])
tab_upload, tab_data, tab_search, tab_stats, tab_profit, tab_delete, tab_sql = tabs

# ---------------- Upload ----------------
with tab_upload:
    st.subheader("Upload Kilimall export files")
    source = st.selectbox(
        "Source / Sheet type",
        ["Kilimall Orders", "Shop Report", "Manual Upload"],
    )
    update_on_conflict = st.checkbox(
        "Update existing rows on conflict (reconciliation mode)", value=False,
        help="If unchecked, duplicates are skipped. If checked, existing orders are updated with new values."
    )
    files = st.file_uploader(
        "Drop CSV or Excel files here",
        type=["csv", "xlsx", "xls"],
        accept_multiple_files=True,
    )

    if files and st.button("Process & Save", type="primary"):
        total_in, total_up, total_skip = 0, 0, 0
        for f in files:
            try:
                raw = read_any(f)
                norm = normalize_columns(raw)
                mapped = map_columns(norm)
                if "order_no" in mapped.columns:
                    mapped["order_no"] = clean_order_string(mapped["order_no"])
                ins, upd, skp = upsert_orders(mapped, logged_via=source, update_on_conflict=update_on_conflict)
                total_in += ins
                total_up += upd
                total_skip += skp
                st.success(f"{f.name}: inserted {ins}, updated {upd}, skipped {skp}")
            except Exception as e:
                st.error(f"{f.name}: {e}")
        st.info(f"Done. Inserted: {total_in} | Updated: {total_up} | Skipped: {total_skip}")

# ---------------- Master Orders ----------------
with tab_data:
    st.subheader("Master Orders")
    df = fetch_all()
    page_size = st.selectbox("Rows per page", [25, 50, 100, 250, 500], index=2, key="page_size")
    total = len(df)
    pages = max(1, (total + page_size - 1) // page_size)
    page = st.number_input("Page", min_value=1, max_value=pages, value=1, key="page_num")
    start = (page - 1) * page_size
    st.caption(f"Showing {start + 1}–{min(start + page_size, total)} of {total}")
    st.dataframe(df.iloc[start:start + page_size], use_container_width=True, height=500, hide_index=True)

    col1, col2 = st.columns(2)
    with col1:
        if not df.empty:
            csv = df.to_csv(index=False).encode("utf-8")
            st.download_button("⬇️ Download CSV", csv, "master_orders.csv", "text/csv")
    with col2:
        if not df.empty:
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as w:
                df.to_excel(w, index=False, sheet_name="Orders")
            st.download_button(
                "⬇️ Download Excel", buf.getvalue(), "master_orders.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

# ---------------- Search ----------------
with tab_search:
    st.subheader("Search & Filter")
    c1, c2, c3 = st.columns(3)
    with c1:
        search_shops = st.multiselect("Shop", distinct_values("shop_name"), key="search_shop")
    with c2:
        search_statuses = st.multiselect("Status", distinct_values("status"), key="search_status")
    with c3:
        search_sources = st.multiselect("Source", distinct_values("logged_via"), key="search_source")

    text_q = st.text_input("Order # / product contains", key="search_text")
    d1, d2 = st.columns(2)
    with d1:
        date_from = st.date_input("From", value=None, key="search_from")
    with d2:
        date_to = st.date_input("To", value=None, key="search_to")

    where, params = [], []
    if search_shops:
        where.append(f"shop_name IN ({','.join(['?']*len(search_shops))})")
        params += search_shops
    if search_statuses:
        where.append(f"status IN ({','.join(['?']*len(search_statuses))})")
        params += search_statuses
    if search_sources:
        where.append(f"logged_via IN ({','.join(['?']*len(search_sources))})")
        params += search_sources
    if text_q:
        where.append("(order_no LIKE ? OR product_name LIKE ?)")
        params += [f"%{text_q}%", f"%{text_q}%"]
    if date_from:
        where.append("order_time >= ?")
        params.append(str(date_from))
    if date_to:
        where.append("order_time <= ?")
        params.append(str(date_to) + " 23:59:59")

    q = "SELECT * FROM master_orders"
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY created_at DESC LIMIT 1000"

    result = run_select(q, tuple(params))
    st.caption(f"{len(result)} rows (max 1000)")
    st.dataframe(result, use_container_width=True, hide_index=True)
    if not result.empty:
        st.download_button(
            "⬇️ Export results", result.to_csv(index=False).encode("utf-8"),
            "search_results.csv", "text/csv"
        )

# ---------------- Stats ----------------
with tab_stats:
    df = fetch_all()
    if df.empty:
        st.info("No data yet. Upload some files first.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Orders", len(df))
        c2.metric("Total Quantity", int(df["quantity"].fillna(0).sum()))
        c3.metric("Total Amount", f"{df['amount'].fillna(0).sum():,.2f}")
        c4.metric("Unique Shops", df["shop_name"].nunique())

        st.subheader("Orders by Shop")
        st.bar_chart(df.groupby("shop_name")["order_no"].count())
        st.subheader("Amount by Status")
        st.bar_chart(df.groupby("status")["amount"].sum())

        st.subheader("Daily GMV Trend")
        try:
            tmp = df.copy()
            tmp["day"] = pd.to_datetime(tmp["order_time"], errors="coerce").dt.date
            daily = tmp.dropna(subset=["day"]).groupby("day")["amount"].sum()
            if not daily.empty:
                st.line_chart(daily)
        except Exception as e:
            st.caption(f"Trend unavailable: {e}")

        st.subheader("Top Products")
        top_n = st.slider("Top N", 5, 50, 10, key="topn_products")
        st.dataframe(
            df.groupby("product_name").agg(orders=("order_no", "count"), revenue=("amount", "sum"))
              .sort_values("revenue", ascending=False).head(top_n),
            use_container_width=True,
        )

# ---------------- Profitability ----------------
with tab_profit:
    st.subheader("Profitability Calculator")
    df = fetch_all()
    if df.empty:
        st.info("No data yet.")
    else:
        margin = st.slider("Assumed margin % (used if cost is 0)", 0, 90, 20, key="margin_pct")
        tmp = df.copy()
        tmp["amount"] = tmp["amount"].fillna(0)
        tmp["cost"] = tmp["cost"].fillna(0)
        tmp["profit"] = tmp.apply(
            lambda r: (r["amount"] - r["cost"]) if r["cost"] > 0 else r["amount"] * (margin / 100),
            axis=1,
        )
        c1, c2, c3 = st.columns(3)
        c1.metric("Revenue", f"{tmp['amount'].sum():,.2f}")
        c2.metric("Cost", f"{tmp['cost'].sum():,.2f}")
        c3.metric("Est. Profit", f"{tmp['profit'].sum():,.2f}")
        st.dataframe(
            tmp.groupby("shop_name").agg(revenue=("amount", "sum"), cost=("cost", "sum"), profit=("profit", "sum"))
               .sort_values("profit", ascending=False),
            use_container_width=True,
        )

# ---------------- Delete ----------------
with tab_delete:
    st.subheader("🗑️ Delete records (permanent)")
    st.warning("Deletes are permanent. A CSV backup of matched rows is offered before deletion.")

    mode = st.radio("Mode", ["Guided filters", "Custom SQL WHERE"], horizontal=True, key="del_mode")

    where_sql, params = "", []
    if mode == "Guided filters":
        c1, c2, c3 = st.columns(3)
        with c1:
            del_shops = st.multiselect("Shop", distinct_values("shop_name"), key="del_shop")
        with c2:
            del_statuses = st.multiselect("Status", distinct_values("status"), key="del_status")
        with c3:
            del_sources = st.multiselect("Source", distinct_values("logged_via"), key="del_source")

        del_text = st.text_input("Order # / product contains", key="del_text")
        d1, d2 = st.columns(2)
        with d1:
            del_from = st.date_input("From", value=None, key="del_from")
        with d2:
            del_to = st.date_input("To", value=None, key="del_to")

        clauses = []
        if del_shops:
            clauses.append(f"shop_name IN ({','.join(['?']*len(del_shops))})")
            params += del_shops
        if del_statuses:
            clauses.append(f"status IN ({','.join(['?']*len(del_statuses))})")
            params += del_statuses
        if del_sources:
            clauses.append(f"logged_via IN ({','.join(['?']*len(del_sources))})")
            params += del_sources
        if del_text:
            clauses.append("(order_no LIKE ? OR product_name LIKE ?)")
            params += [f"%{del_text}%", f"%{del_text}%"]
        if del_from:
            clauses.append("order_time >= ?")
            params.append(str(del_from))
        if del_to:
            clauses.append("order_time <= ?")
            params.append(str(del_to) + " 23:59:59")

        where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    else:
        raw_where = st.text_area(
            "Custom WHERE clause (without the word WHERE)",
            placeholder="e.g.  status = 'cancelled' AND amount < 100",
            key="del_custom_where",
        )
        if raw_where.strip():
            where_sql = " WHERE " + raw_where.strip().rstrip(";")

    preview_q = "SELECT * FROM master_orders" + where_sql + " LIMIT 1000"
    try:
        preview = run_select(preview_q, tuple(params))
    except Exception as e:
        preview = pd.DataFrame({"error": [str(e)]})

    count_q = "SELECT COUNT(*) AS n FROM master_orders" + where_sql
    try:
        match_n = int(run_select(count_q, tuple(params))["n"].iloc[0])
    except Exception:
        match_n = 0

    st.info(f"Matched rows: **{match_n}**")
    st.dataframe(preview, use_container_width=True, hide_index=True)

    if not preview.empty and "error" not in preview.columns:
        st.download_button(
            "⬇️ Download backup of matched rows",
            preview.to_csv(index=False).encode("utf-8"),
            f"delete_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            "text/csv",
        )

    st.markdown("---")
    confirm = st.text_input("Type **DELETE** to confirm filtered deletion, or **WIPE ALL** to clear entire table",
                            key="del_confirm")
    cdel1, cdel2 = st.columns(2)
    with cdel1:
        if st.button("🗑️ Delete matched", type="primary", disabled=(match_n == 0 and confirm != "WIPE ALL")):
            if confirm == "DELETE" and where_sql:
                conn = get_conn()
                conn.execute("DELETE FROM master_orders" + where_sql, tuple(params))
                conn.commit()
                conn.close()
                log_audit("delete_filtered", where_sql, match_n)
                st.success(f"Deleted {match_n} rows.")
            elif confirm == "WIPE ALL":
                conn = get_conn()
                n = conn.execute("SELECT COUNT(*) FROM master_orders").fetchone()[0]
                conn.execute("DELETE FROM master_orders")
                conn.commit()
                conn.close()
                log_audit("wipe_all", "entire table", n)
                st.warning(f"Wiped all {n} rows.")
            else:
                st.error("Confirmation text does not match, or no filter set.")

    with st.expander("📜 Recent audit log"):
        st.dataframe(
            run_select("SELECT * FROM audit_log ORDER BY created_at DESC LIMIT 100"),
            use_container_width=True, hide_index=True,
        )

# ---------------- SQL Console ----------------
with tab_sql:
    st.subheader("Read-only SQL Console (SELECT only)")
    default_q = "SELECT shop_name, COUNT(*) AS orders, SUM(amount) AS revenue FROM master_orders GROUP BY shop_name ORDER BY revenue DESC LIMIT 50"
    query = st.text_area("Query", value=default_q, height=120, key="sql_query")
    if st.button("Run query", key="run_sql"):
        result = run_safe_select(query)
        st.write(f"**{len(result)} rows**")
        st.dataframe(result, hide_index=True, use_container_width=True)
        if not result.empty and "error" not in result.columns:
            csv = result.to_csv(index=False).encode("utf-8")
            st.download_button("📥 Export", csv, "query_results.csv", "text/csv")

st.caption(f"DB file: {DB_FILE} • Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}")
