import streamlit as st
import pandas as pd
import sqlite3
import os
import re
import io
from datetime import datetime
from difflib import get_close_matches

# ---------------- Page Config ----------------
st.set_page_config(
    page_title="EDITECH Automation Tracker",
    layout="wide",
    page_icon="🚀",
    initial_sidebar_state="expanded",
)

# ---------------- Styling ----------------
st.markdown("""
<style>
    .main .block-container { padding-top: 2rem; max-width: 1400px; }
    .stMetric { background: #f8f9fb; padding: 1rem; border-radius: 12px; border: 1px solid #eef0f4; }
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .stTabs [data-baseweb="tab"] { border-radius: 8px 8px 0 0; padding: 10px 20px; }
    div[data-testid="stFileUploader"] { background: #fafbfc; border-radius: 12px; padding: 1rem; }
    h1, h2, h3 { color: #1f2937; }
</style>
""", unsafe_allow_html=True)

# ---------------- DB ----------------
DB_DIR = os.environ.get("DB_DIR", "/data")
try:
    os.makedirs(DB_DIR, exist_ok=True)
except PermissionError:
    DB_DIR = "./data"
    os.makedirs(DB_DIR, exist_ok=True)
DB_FILE = os.path.join(DB_DIR, "kilimall_automation.db")

def get_conn():
    return sqlite3.connect(DB_FILE, check_same_thread=False)

def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS master_orders (
                order_no TEXT PRIMARY KEY,
                shop_id TEXT, shop_name TEXT, product_name TEXT,
                quantity INTEGER, amount REAL,
                order_time TEXT, status TEXT, logged_via TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
init_db()

# ---------------- Column Detection ----------------
COLUMN_ALIASES = {
    "order_no":    ["order_no", "order_number", "order_id", "orderno", "order", "ordernumber", "order#", "no"],
    "shop_id":     ["shop_id", "shopid", "store_id", "seller_id"],
    "shop_name":   ["shop_name", "shop", "store_name", "store", "seller", "seller_name"],
    "product_name":["product_name", "product", "item", "item_name", "sku_name", "goods_name"],
    "quantity":    ["quantity", "qty", "count", "units", "num"],
    "amount":      ["amount", "total", "price", "total_amount", "total_price", "gmv", "revenue"],
    "order_time":  ["order_time", "order_date", "date", "created_at", "created", "time", "placed_at"],
    "status":      ["status", "order_status", "state"],
}
REQUIRED = ["order_no"]

def normalize(col: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(col).lower())

def auto_map(df_cols):
    """Return {target: source_col or None} using exact + fuzzy match."""
    norm_to_orig = {normalize(c): c for c in df_cols}
    norm_keys = list(norm_to_orig.keys())
    mapping = {}
    for target, aliases in COLUMN_ALIASES.items():
        found = None
        for a in aliases:
            n = normalize(a)
            if n in norm_to_orig:
                found = norm_to_orig[n]; break
        if not found:
            for a in aliases:
                close = get_close_matches(normalize(a), norm_keys, n=1, cutoff=0.8)
                if close:
                    found = norm_to_orig[close[0]]; break
        mapping[target] = found
    return mapping

def clean_order(series: pd.Series) -> pd.Series:
    return (series.astype(str)
            .str.replace(r"\.0$", "", regex=True)
            .str.replace(r"[^\w-]", "", regex=True)
            .str.strip())

def read_any(file) -> pd.DataFrame:
    name = file.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(file, dtype=str, keep_default_na=False, na_values=[""])
    return pd.read_excel(file, dtype=str)

def apply_mapping(df: pd.DataFrame, mapping: dict) -> pd.DataFrame:
    out = pd.DataFrame()
    for target, source in mapping.items():
        out[target] = df[source] if source and source in df.columns else None
    return out

def upsert_batch(df: pd.DataFrame, logged_via: str, progress=None) -> tuple[int, int]:
    rows, skipped = [], 0
    for _, r in df.iterrows():
        o = r.get("order_no")
        if not o or pd.isna(o) or str(o).strip() == "":
            skipped += 1; continue
        try:
            qty = int(float(r["quantity"])) if pd.notna(r.get("quantity")) and str(r.get("quantity")).strip() else 0
        except (ValueError, TypeError): qty = 0
        try:
            amt = float(r["amount"]) if pd.notna(r.get("amount")) and str(r.get("amount")).strip() else 0.0
        except (ValueError, TypeError): amt = 0.0
        rows.append((str(o), str(r.get("shop_id") or ""), str(r.get("shop_name") or ""),
                     str(r.get("product_name") or ""), qty, amt,
                     str(r.get("order_time") or ""), str(r.get("status") or ""), logged_via))

    inserted = 0
    with get_conn() as conn:
        c = conn.cursor()
        batch = 500
        for i in range(0, len(rows), batch):
            chunk = rows[i:i+batch]
            before = conn.total_changes
            c.executemany("""INSERT OR IGNORE INTO master_orders
                (order_no, shop_id, shop_name, product_name, quantity, amount, order_time, status, logged_via)
                VALUES (?,?,?,?,?,?,?,?,?)""", chunk)
            inserted += conn.total_changes - before
            if progress:
                progress.progress(min(1.0, (i+batch)/max(1,len(rows))))
    skipped += len(rows) - inserted
    return inserted, skipped

def fetch_all() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query("SELECT * FROM master_orders ORDER BY created_at DESC", conn)

# ---------------- Sidebar ----------------
with st.sidebar:
    st.markdown("### ⚙️ Settings")
    source = st.selectbox("Source / Sheet type",
        ["Kilimall Orders", "Shop Report", "Manual Upload"])
    st.divider()
    st.caption(f"📁 DB: `{DB_FILE}`")
    st.caption(f"🕒 {datetime.now().strftime('%Y-%m-%d %H:%M')}")

# ---------------- Header ----------------
st.title("📊 EDITECH DIGITAL")
st.markdown("**Kilimall Automation Software** — Upload raw exports, get clean data.")
st.divider()

tab_upload, tab_data, tab_stats = st.tabs(["📤 Upload", "📋 Master Orders", "📈 Stats"])

# ---------------- Upload Tab ----------------
with tab_upload:
    files = st.file_uploader(
        "Drop CSV or Excel files here",
        type=["csv", "xlsx", "xls"],
        accept_multiple_files=True,
    )

    if files:
        st.markdown("#### 🔍 Preview & Column Mapping")
        previews = {}
        mappings = {}

        for f in files:
            with st.expander(f"📄 {f.name}", expanded=len(files) == 1):
                try:
                    f.seek(0)
                    raw = read_any(f)
                    previews[f.name] = raw
                    st.caption(f"{len(raw):,} rows · {len(raw.columns)} columns")
                    st.dataframe(raw.head(5), use_container_width=True, height=200)

                    auto = auto_map(raw.columns.tolist())
                    st.markdown("**Column mapping** _(auto-detected, override if wrong)_")
                    cols = st.columns(4)
                    user_map = {}
                    options = ["— None —"] + list(raw.columns)
                    for i, (target, src) in enumerate(auto.items()):
                        with cols[i % 4]:
                            idx = options.index(src) if src in options else 0
                            picked = st.selectbox(
                                f"`{target}`" + (" *" if target in REQUIRED else ""),
                                options, index=idx, key=f"{f.name}_{target}")
                            user_map[target] = None if picked == "— None —" else picked
                    mappings[f.name] = user_map

                    missing = [t for t in REQUIRED if not user_map.get(t)]
                    if missing:
                        st.warning(f"⚠️ Missing required: {', '.join(missing)}")
                except Exception as e:
                    st.error(f"Failed to read: {e}")

        st.divider()
        if st.button("🚀 Process & Save", type="primary", use_container_width=True):
            total_in, total_skip = 0, 0
            progress = st.progress(0.0)
            status = st.empty()

            for f in files:
                if f.name not in previews: continue
                user_map = mappings[f.name]
                if not user_map.get("order_no"):
                    st.error(f"{f.name}: skipped — no `order_no` mapped"); continue

                status.info(f"Processing {f.name}…")
                mapped = apply_mapping(previews[f.name], user_map)
                mapped["order_no"] = clean_order(mapped["order_no"])
                mapped = mapped[mapped["order_no"].astype(bool) & (mapped["order_no"] != "")]

                ins, skp = upsert_batch(mapped, logged_via=source, progress=progress)
                total_in += ins; total_skip += skp
                st.success(f"✅ **{f.name}** — inserted {ins:,} · skipped {skp:,}")

            progress.progress(1.0); status.empty()
            st.balloons()
            st.info(f"**Done.** Total inserted: **{total_in:,}** · skipped (duplicates/invalid): **{total_skip:,}**")

# ---------------- Data Tab ----------------
with tab_data:
    df = fetch_all()
    top1, top2, top3 = st.columns([2, 1, 1])
    top1.markdown(f"### Master Orders &nbsp; `{len(df):,} records`")

    if not df.empty:
        search = top2.text_input("🔎 Search", placeholder="order, shop, product…")
        status_filter = top3.selectbox("Status", ["All"] + sorted(df["status"].dropna().unique().tolist()))
        view = df.copy()
        if search:
            mask = view.apply(lambda r: r.astype(str).str.contains(search, case=False, na=False).any(), axis=1)
            view = view[mask]
        if status_filter != "All":
            view = view[view["status"] == status_filter]
        st.dataframe(view, use_container_width=True, height=520)

        d1, d2, _ = st.columns([1, 1, 4])
        d1.download_button("⬇️ CSV", view.to_csv(index=False).encode("utf-8"),
                           "master_orders.csv", "text/csv", use_container_width=True)
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            view.to_excel(w, index=False, sheet_name="Orders")
        d2.download_button("⬇️ Excel", buf.getvalue(), "master_orders.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True)
    else:
        st.info("No data yet. Upload some files in the Upload tab.")

    with st.expander("⚠️ Danger zone"):
        confirm = st.text_input("Type DELETE to confirm")
        if st.button("Delete ALL records", disabled=(confirm != "DELETE")):
            with get_conn() as conn: conn.execute("DELETE FROM master_orders")
            st.warning("All records deleted. Refresh the page.")

# ---------------- Stats Tab ----------------
with tab_stats:
    df = fetch_all()
    if df.empty:
        st.info("No data yet. Upload some files first.")
    else:
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0)
        df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Orders", f"{len(df):,}")
        c2.metric("Total Quantity", f"{int(df['quantity'].sum()):,}")
        c3.metric("Total Amount", f"KSh {df['amount'].sum():,.0f}")
        c4.metric("Unique Shops", df["shop_name"].replace("", pd.NA).nunique())
        st.divider()
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("##### Orders by Shop")
            st.bar_chart(df[df["shop_name"] != ""].groupby("shop_name")["order_no"].count())
        with col2:
            st.markdown("##### Amount by Status")
            st.bar_chart(df[df["status"] != ""].groupby("status")["amount"].sum())
