import streamlit as st
import pandas as pd
import sqlite3
import os
from datetime import datetime
import io

# ---------------- Page Configuration ----------------
st.set_page_config(
    page_title="EDITECH Automation Tracker",
    layout="wide",
    page_icon="🚀",
)

# ---------------- Database Setup ----------------
# Use a persistent volume path when running in Docker/Coolify
DB_DIR = os.environ.get("DB_DIR", "/data")
os.makedirs(DB_DIR, exist_ok=True)
DB_FILE = os.path.join(DB_DIR, "kilimall_automation.db")


def get_conn():
    return sqlite3.connect(DB_FILE, check_same_thread=False)


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
            order_time TEXT,
            status TEXT,
            logged_via TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    conn.close()


init_db()


# ---------------- Helpers ----------------
def clean_order_string(series: pd.Series) -> pd.Series:
    """Safely cleans and standardizes order numbers from any sheet format."""
    return series.astype(str).str.replace(r"[^\d]", "", regex=True).str.strip()


def read_any(file) -> pd.DataFrame:
    """Read CSV or Excel into a DataFrame."""
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


def upsert_orders(df: pd.DataFrame, logged_via: str) -> tuple[int, int]:
    """Insert new orders, ignoring duplicates. Returns (inserted, skipped)."""
    conn = get_conn()
    c = conn.cursor()
    inserted, skipped = 0, 0
    for _, row in df.iterrows():
        order_no = row.get("order_no")
        if not order_no:
            skipped += 1
            continue
        try:
            c.execute(
                """
                INSERT INTO master_orders
                (order_no, shop_id, shop_name, product_name, quantity, amount, order_time, status, logged_via)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(order_no),
                    str(row.get("shop_id") or ""),
                    str(row.get("shop_name") or ""),
                    str(row.get("product_name") or ""),
                    int(row["quantity"]) if pd.notna(row.get("quantity")) else 0,
                    float(row["amount"]) if pd.notna(row.get("amount")) else 0.0,
                    str(row.get("order_time") or ""),
                    str(row.get("status") or ""),
                    logged_via,
                ),
            )
            inserted += 1
        except sqlite3.IntegrityError:
            skipped += 1
    conn.commit()
    conn.close()
    return inserted, skipped


def fetch_all() -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query("SELECT * FROM master_orders ORDER BY created_at DESC", conn)
    conn.close()
    return df


# ---------------- UI ----------------
st.title("📊 EDITECH DIGITAL — Kilimall Automation Software")
st.markdown(
    "Upload your raw Kilimall files directly. No manual typing or data manipulation needed."
)

tab_upload, tab_data, tab_stats = st.tabs(["📤 Upload", "📋 Master Orders", "📈 Stats"])

with tab_upload:
    st.subheader("Upload Kilimall export files")
    source = st.selectbox(
        "Source / Sheet type",
        ["Kilimall Orders", "Shop Report", "Manual Upload"],
    )
    files = st.file_uploader(
        "Drop CSV or Excel files here",
        type=["csv", "xlsx", "xls"],
        accept_multiple_files=True,
    )

    if files and st.button("Process & Save", type="primary"):
        total_in, total_skip = 0, 0
        for f in files:
            try:
                raw = read_any(f)
                norm = normalize_columns(raw)
                mapped = map_columns(norm)
                if "order_no" in mapped.columns:
                    mapped["order_no"] = clean_order_string(mapped["order_no"])
                ins, skp = upsert_orders(mapped, logged_via=source)
                total_in += ins
                total_skip += skp
                st.success(f"{f.name}: inserted {ins}, skipped {skp}")
            except Exception as e:
                st.error(f"{f.name}: {e}")
        st.info(f"Done. Total inserted: {total_in} | Total skipped: {total_skip}")

with tab_data:
    st.subheader("Master Orders")
    df = fetch_all()
    st.dataframe(df, use_container_width=True, height=500)

    col1, col2 = st.columns(2)
    with col1:
        if not df.empty:
            csv = df.to_csv(index=False).encode("utf-8")
            st.download_button(
                "⬇️ Download CSV", csv, "master_orders.csv", "text/csv"
            )
    with col2:
        if not df.empty:
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as w:
                df.to_excel(w, index=False, sheet_name="Orders")
            st.download_button(
                "⬇️ Download Excel",
                buf.getvalue(),
                "master_orders.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

    with st.expander("⚠️ Danger zone"):
        if st.button("Delete ALL records"):
            conn = get_conn()
            conn.execute("DELETE FROM master_orders")
            conn.commit()
            conn.close()
            st.warning("All records deleted. Reload the page.")

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

st.caption(f"DB file: {DB_FILE} • Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}")
