"""
EDITECH DIGITAL — Kilimall Order Lifecycle, Reconciliation & BI Software
========================================================================
A state-driven Streamlit application backed by SQLite for managing the
full Kilimall order lifecycle: daily order capture, weekly settlement
reconciliation, exception handling, and lifetime business intelligence.
Now features full Code-Free UI Dynamic Shop Management.
"""

from __future__ import annotations

import io
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from typing import Iterable

import pandas as pd
import streamlit as st

# ---------------------------------------------------------------------------
# Page configuration
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Kilimall Reconciliation Suite — EDITECH DIGITAL",
    layout="wide",
    page_icon="📦",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------
DB_DIR = os.environ.get("DB_DIR", "/app/data")
os.makedirs(DB_DIR, exist_ok=True)
DB_FILE = os.path.join(DB_DIR, "reconciliation.db")


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        c = conn.cursor()
        
        # 1. New dynamic table to store shops via the UI
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS managed_shops (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shop_name TEXT UNIQUE NOT NULL,
                aliases TEXT
            )
            """
        )
        
        # Seed initial 5 shops if the table is freshly created
        c.execute("SELECT COUNT(*) as cnt FROM managed_shops")
        if c.fetchone()["cnt"] == 0:
            initial_shops = [
                ("EDITECH DIGITAL", "EDITECH, DIGITAL"),
                ("DACELY STORE", "DACELY, STORE"),
                ("TANIAH", "TANIAH"),
                ("EDYTECH", "EDYTECH"),
                ("GMD ALISON", "GMD, ALISON")
            ]
            c.executemany("INSERT INTO managed_shops (shop_name, aliases) VALUES (?, ?)", initial_shops)
        
        # 2. Verify active_daily_orders columns
        c.execute("PRAGMA table_info(active_daily_orders)")
        columns = [row["name"] for row in c.fetchall()]
        if not columns:
            c.execute(
                """
                CREATE TABLE active_daily_orders (
                    date TEXT,
                    order_no TEXT PRIMARY KEY,
                    shop_name TEXT DEFAULT 'EDITECH DIGITAL',
                    goods_name TEXT,
                    qty INTEGER,
                    selling_price REAL
                )
                """
            )
        elif "shop_name" not in columns:
            c.execute("ALTER TABLE active_daily_orders ADD COLUMN shop_name TEXT DEFAULT 'EDITECH DIGITAL'")

        # 3. Verify unkeyed_buffer columns
        c.execute("PRAGMA table_info(unkeyed_buffer)")
        columns = [row["name"] for row in c.fetchall()]
        if not columns:
            c.execute(
                """
                CREATE TABLE unkeyed_buffer (
                    order_no TEXT PRIMARY KEY,
                    shop_name TEXT DEFAULT 'EDITECH DIGITAL',
                    settlement_period TEXT,
                    complete_amount REAL,
                    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        elif "shop_name" not in columns:
            c.execute("ALTER TABLE unkeyed_buffer ADD COLUMN shop_name TEXT DEFAULT 'EDITECH DIGITAL'")

        # 4. Verify historical_archive columns
        c.execute("PRAGMA table_info(historical_archive)")
        columns = [row["name"] for row in c.fetchall()]
        if not columns:
            c.execute(
                """
                CREATE TABLE historical_archive (
                    order_no TEXT PRIMARY KEY,
                    shop_name TEXT DEFAULT 'EDITECH DIGITAL',
                    goods_name TEXT,
                    qty INTEGER,
                    selling_price REAL,
                    settlement_period TEXT,
                    complete_amount REAL,
                    commission REAL,
                    ds_processing_fee REAL,
                    fines REAL,
                    other_deductions REAL,
                    net_payout REAL,
                    archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        elif "shop_name" not in columns:
            c.execute("ALTER TABLE historical_archive ADD COLUMN shop_name TEXT DEFAULT 'EDITECH DIGITAL'")


init_db()


# ---------------------------------------------------------------------------
# Dynamic Shop Fetchers & Data Normalization
# ---------------------------------------------------------------------------
def get_dynamic_shops() -> list[str]:
    """Fetch the latest live list of verified shops from the database."""
    with get_conn() as conn:
        rows = conn.execute("SELECT shop_name FROM managed_shops ORDER BY shop_name").fetchall()
        return [r["shop_name"] for r in rows]


_DIGIT_RE = re.compile(r"\D+")


def clean_order_no(value) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return ""
    return _DIGIT_RE.sub("", s)


def clean_order_series(series: pd.Series) -> pd.Series:
    return series.map(clean_order_no)


def normalize_shop_name(val) -> str:
    """Intelligently matches uploads to UI-defined stores using database rules."""
    shops = get_dynamic_shops()
    if not shops:
        return "EDITECH DIGITAL"
        
    if pd.isna(val) or not str(val).strip():
        return shops[0]
        
    s = str(val).strip().upper()
    
    # Read rules directly from database records
    with get_conn() as conn:
        rules = conn.execute("SELECT shop_name, aliases FROM managed_shops").fetchall()
        
    # Phase 1: Check full string or alias token hits
    for r in rules:
        name = r["shop_name"].upper()
        if s == name or name in s or s in name:
            return r["shop_name"]
            
        if r["aliases"]:
            aliases = [a.strip().upper() for a in r["aliases"].split(",") if a.strip()]
            for alias in aliases:
                if alias in s:
                    return r["shop_name"]
                    
    return shops[0]  # System fallback to first alphabetical store entry


def to_float(v) -> float:
    try:
        if pd.isna(v):
            return 0.0
    except Exception:
        pass
    try:
        return float(str(v).replace(",", "").replace("KSH", "").replace("ksh", "").strip() or 0)
    except Exception:
        return 0.0


def find_column(df: pd.DataFrame, candidates: Iterable[str]) -> str | None:
    norm = {re.sub(r"[\s_\(\)（）]+", "", c).lower(): c for c in df.columns.astype(str)}
    for cand in candidates:
        key = re.sub(r"[\s_\(\)（）]+", "", cand).lower()
        if key in norm:
            return norm[key]
    return None


def find_sheet(sheets: dict[str, pd.DataFrame], candidates: Iterable[str]) -> str | None:
    norm = {re.sub(r"\s+", "", n).lower(): n for n in sheets.keys()}
    for cand in candidates:
        key = re.sub(r"\s+", "", cand).lower()
        if key in norm:
            return norm[key]
    return None


def read_table(query: str, params: tuple = ()) -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query(query, conn, params=params)


def ksh(x: float) -> str:
    return f"KSH {x:,.2f}"


# ---------------------------------------------------------------------------
# SIDEBAR — Global Shop Filtering Control
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("🏪 Global Shop Control")
    st.caption("Select a shop view to filter statistics and look up localized ledgers across tables.")
    
    # Load live shop dropdown list dynamically
    live_shops = get_dynamic_shops()
    selected_shop = st.selectbox(
        "🎯 Filter Views by Shop Name",
        options=["All Shops"] + live_shops,
        index=0,
        help="Filters the business scorecard and active ledger displays. Data imports maintain individual row attributions."
    )

# ---------------------------------------------------------------------------
# Core Data Fetching under Scope Segregation Constraints
# ---------------------------------------------------------------------------
if selected_shop == "All Shops":
    active_df = read_table("SELECT * FROM active_daily_orders ORDER BY date DESC")
    archive_df = read_table("SELECT * FROM historical_archive ORDER BY archived_at DESC")
    buffer_df = read_table("SELECT * FROM unkeyed_buffer ORDER BY detected_at DESC")
else:
    active_df = read_table("SELECT * FROM active_daily_orders WHERE shop_name = ? ORDER BY date DESC", (selected_shop,))
    archive_df = read_table("SELECT * FROM historical_archive WHERE shop_name = ? ORDER BY archived_at DESC", (selected_shop,))
    buffer_df = read_table("SELECT * FROM unkeyed_buffer WHERE shop_name = ? ORDER BY detected_at DESC", (selected_shop,))

# ---------------------------------------------------------------------------
# Dashboard Summary Cards Header
# ---------------------------------------------------------------------------
st.subheader("📊 Lifetime Business Scorecard")
total_sold = float(active_df["selling_price"].fillna(0).sum()) + float(archive_df["selling_price"].fillna(0).sum())
total_net_paid = float(archive_df["net_payout"].fillna(0).sum()) if not archive_df.empty else 0.0
total_fees = (
    float(archive_df["commission"].fillna(0).abs().sum())
    + float(archive_df["ds_processing_fee"].fillna(0).abs().sum())
    + float(archive_df["fines"].fillna(0).abs().sum())
    + float(archive_df["other_deductions"].fillna(0).abs().sum())
    if not archive_df.empty
    else 0.0
)
pending_payment = float(active_df["selling_price"].fillna(0).sum())

m1, m2, m3, m4 = st.columns(4)
m1.metric(f"💰 Total Value Sold ({selected_shop})", ksh(total_sold))
m2.metric(f"🏦 Total Net Paid Out ({selected_shop})", ksh(total_net_paid))
m3.metric(f"✂️ Total Fees & Deductions", ksh(total_fees))
m4.metric(f"⏳ Pending Payment Balance", ksh(pending_payment), f"{len(active_df)} open orders")

st.divider()

# ---------------------------------------------------------------------------
# Core Workspaces Tabs Layout Configuration
# ---------------------------------------------------------------------------
tab_ledger, tab_recon, tab_buffer, tab_archive, tab_settings = st.tabs(
    [
        "📝 Daily Ledger Entries",
        "🔄 Reconcile Settlement Report",
        f"⚠️ Un-keyed Exceptions Buffer ({len(buffer_df)})",
        f"📚 Permanent Historical Archive ({len(archive_df)})",
        "⚙️ Storefront & Shop Settings"  # New Code-Free Settings Tab
    ]
)

# ---------------------------------------------------------------------------
# MODULE B — Interactive Grid Ledger (Daily Logs Workspace)
# ---------------------------------------------------------------------------
def _snapshot_active() -> None:
    snap = read_table("SELECT date, order_no, shop_name, goods_name, qty, selling_price FROM active_daily_orders")
    stack = st.session_state.setdefault("undo_stack", [])
    stack.append(snap)
    if len(stack) > 10:
        stack.pop(0)


def _replace_active(df: pd.DataFrame) -> int:
    clean = df.copy()
    if "order_no" not in clean.columns:
        return 0
    clean["order_no"] = clean_order_series(clean["order_no"])
    clean = clean[clean["order_no"].astype(bool)]
    clean = clean.drop_duplicates(subset=["order_no"], keep="last")
    
    rows = [
        (
            str(r.get("date") or date.today().isoformat()),
            str(r["order_no"]),
            normalize_shop_name(r.get("shop_name")),
            str(r.get("goods_name") or ""),
            int(r["qty"]) if pd.notna(r.get("qty")) else 0,
            float(r["selling_price"]) if pd.notna(r.get("selling_price")) else 0.0,
        )
        for _, r in clean.iterrows()
    ]
    
    with get_conn() as conn:
        if selected_shop == "All Shops":
            conn.execute("DELETE FROM active_daily_orders")
            conn.executemany(
                "INSERT INTO active_daily_orders (date, order_no, shop_name, goods_name, qty, selling_price) VALUES (?,?,?,?,?,?)",
                rows,
            )
        else:
            conn.execute("DELETE FROM active_daily_orders WHERE shop_name = ?", (selected_shop,))
            conn.executemany(
                "INSERT INTO active_daily_orders (date, order_no, shop_name, goods_name, qty, selling_price) VALUES (?,?,?,?,?,?)",
                rows,
            )
            
    return len(rows)


with tab_ledger:
    st.subheader("📝 Active Dispatch Daily Ledger Logs")
    
    if not live_shops:
        st.error("No registered stores found. Please define at least one storefront entry under the Settings tab.")
    else:
        # --- Hybrid Upload Engine with Intelligently Structured Defaults ---
        with st.expander("📤 Bulk Load Daily Dispatched Records (Excel / CSV)", expanded=False):
            up_col1, up_col2, up_col3 = st.columns([2, 1, 1])
            upload_file = up_col1.file_uploader(
                "Upload orders file", type=["xlsx", "xls", "csv"], label_visibility="collapsed", key="orders_upload"
            )
            default_upload_shop = up_col2.selectbox("Fallback Shop Target Assignment", options=live_shops)
            clear_range_mode = up_col3.checkbox("Clear Current Filter View Before Saving", value=False)

            if upload_file and st.button("⬆️ Parse and Save Uploaded Sheet", type="primary"):
                try:
                    if upload_file.name.lower().endswith(".csv"):
                        udf = pd.read_csv(upload_file, dtype=str)
                    else:
                        udf = pd.read_excel(upload_file, dtype=str)

                    c_date = find_column(udf, ["date", "order_date", "created_at"])
                    c_ord = find_column(udf, ["order_no", "order_sn", "order", "order number", "order_id"])
                    c_shop = find_column(udf, ["shop_name", "shop", "store_name", "store"])
                    c_goods = find_column(udf, ["goods_name", "product", "product_name", "item", "goods"])
                    c_qty = find_column(udf, ["qty", "quantity", "qnty"])
                    c_price = find_column(udf, ["selling_price", "price", "amount", "selling price"])

                    if not c_ord:
                        st.error("Operation aborted: Missing mandatory 'Order Number' mapping vector column.")
                    else:
                        norm = pd.DataFrame({
                            "date": udf[c_date] if c_date else date.today().isoformat(),
                            "order_no": clean_order_series(udf[c_ord]),
                            "shop_name": udf[c_shop].map(normalize_shop_name) if c_shop else default_upload_shop,
                            "goods_name": udf[c_goods] if c_goods else "",
                            "qty": udf[c_qty].map(lambda v: int(to_float(v))) if c_qty else 1,
                            "selling_price": udf[c_price].map(to_float) if c_price else 0.0,
                        })
                        norm = norm[norm["order_no"].astype(bool)]

                        _snapshot_active()
                        
                        full_current = read_table("SELECT date, order_no, shop_name, goods_name, qty, selling_price FROM active_daily_orders")
                        if clear_range_mode:
                            if selected_shop == "All Shops":
                                merged = norm
                            else:
                                other_shops = full_current[full_current["shop_name"] != selected_shop]
                                merged = pd.concat([other_shops, norm], ignore_index=True)
                        else:
                            merged = pd.concat([full_current, norm], ignore_index=True)
                            
                        _replace_active(merged)
                        st.toast(f"Successfully appended {len(norm)} rows to tracking profiles.", icon="✅")
                        st.rerun()
                except Exception as exc:
                    st.error(f"Bulk data processing execution failed: {exc}")

        # --- Live Ledger Editor Grid Frame Interface ---
        grid_seed = active_df.copy()
        if grid_seed.empty:
            grid_seed = pd.DataFrame([
                {
                    "date": date.today().isoformat(),
                    "order_no": "",
                    "shop_name": selected_shop if selected_shop != "All Shops" else live_shops[0],
                    "goods_name": "",
                    "qty": 1,
                    "selling_price": 0.0
                }
            ])
        else:
            grid_seed = grid_seed[["date", "order_no", "shop_name", "goods_name", "qty", "selling_price"]]
        grid_seed.insert(0, "_delete", False)

        edited = st.data_editor(
            grid_seed,
            key="ledger_editor",
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "_delete": st.column_config.CheckboxColumn("🗑️", help="Select row elements for deletion commands", default=False),
                "date": st.column_config.TextColumn("Date Capture (YYYY-MM-DD)"),
                "order_no": st.column_config.TextColumn("Order No. (Unique)", required=True),
                "shop_name": st.column_config.SelectboxColumn("Shop Designation Column", options=live_shops, required=True),
                "goods_name": st.column_config.TextColumn("Goods Nomenclature Name"),
                "qty": st.column_config.NumberColumn("Quantity Handled", min_value=0, step=1),
                "selling_price": st.column_config.NumberColumn("Calculated Selling Price (KSH)", format="%.2f"),
            },
        )

        undo_stack = st.session_state.get("undo_stack", [])
        col_a, col_b, col_c, _ = st.columns([1.5, 1.5, 1, 3])

        if col_a.button("💾 Commit Ledger Updates", type="primary", use_container_width=True):
            try:
                _snapshot_active()
                keep = edited[~edited["_delete"].fillna(False)].drop(columns=["_delete"])
                
                full_current = read_table("SELECT date, order_no, shop_name, goods_name, qty, selling_price FROM active_daily_orders")
                if selected_shop == "All Shops":
                    merged = keep
                else:
                    other_shops = full_current[full_current["shop_name"] != selected_shop]
                    merged = pd.concat([other_shops, keep], ignore_index=True)
                    
                _replace_active(merged)
                st.toast("Active Ledger alterations stored safely.", icon="✅")
                st.rerun()
            except Exception as exc:
                st.error(f"Failed to push grid corrections: {exc}")

        if col_b.button("🗑️ Delete Selected Orders", use_container_width=True):
            to_del = edited[edited["_delete"].fillna(False)]
            if to_del.empty:
                st.toast("No ledger lines selected for deletion.", icon="ℹ️")
            else:
                _snapshot_active()
                ids = [clean_order_no(x) for x in to_del["order_no"].tolist() if clean_order_no(x)]
                with get_conn() as conn:
                    conn.executemany("DELETE FROM active_daily_orders WHERE order_no = ?", [(i,) for i in ids])
                st.toast(f"Purged {len(ids)} target lines from operational view logs.", icon="🗑️")
                st.rerun()

        if col_c.button(f"↩️ Undo ({len(undo_stack)})", use_container_width=True, disabled=not undo_stack):
            prev = st.session_state["undo_stack"].pop()
            _replace_active(prev)
            st.toast("Reverted state to last saved database footprint frame.", icon="↩️")
            st.rerun()

# ---------------------------------------------------------------------------
# MODULE C — Multi-sheet Reconciliation Engine
# ---------------------------------------------------------------------------
def run_reconciliation(file, settlement_period: str) -> dict:
    sheets = pd.read_excel(file, sheet_name=None, dtype=str)
    warnings: list[str] = []

    bill_name = find_sheet(sheets, ["bill details", "billdetails", "bill_details", "bill detail"])
    if not bill_name:
        raise ValueError("Critical structural component missing: 'bill details' sheet wasn't discovered.")
    bill = sheets[bill_name].copy()

    col_order = find_column(bill, ["order_sn", "order_no", "order sn", "order number"])
    col_amount = find_column(bill, ["complete amount", "completeamount", "complete_amount", "amount"])
    col_comm = find_column(bill, ["Commission", "commission"])
    col_settle = find_column(bill, ["settlement", "settle"])
    col_store_bill = find_column(bill, ["store_name", "storeName", "store", "shop_name"])
    
    if not col_order or not col_amount:
        raise ValueError("Missing critical core linking identity columns inside verification sheets.")

    bill_df = pd.DataFrame(
        {
            "order_no": clean_order_series(bill[col_order]),
            "complete_amount": bill[col_amount].map(to_float),
            "commission": bill[col_comm].map(to_float) if col_comm else 0.0,
            "settlement_base": bill[col_settle].map(to_float) if col_settle else 0.0,
            "shop_name": bill[col_store_bill].map(normalize_shop_name) if col_store_bill else "EDITECH DIGITAL",
        }
    )
    bill_df = bill_df[bill_df["order_no"].astype(bool)]
    bill_df = bill_df.groupby("order_no", as_index=False).agg(
        {"complete_amount": "sum", "commission": "sum", "settlement_base": "sum", "shop_name": "first"}
    )

    ds_name = find_sheet(sheets, ["ds processing fee", "dsprocessingfee", "ds_processing_fee"])
    if ds_name:
        ds = sheets[ds_name]
        c_o = find_column(ds, ["order_no", "order_sn", "order"])
        c_a = find_column(ds, ["amout", "amount"])
        if c_o and c_a:
            ds_df = pd.DataFrame({"order_no": clean_order_series(ds[c_o]), "ds_processing_fee": ds[c_a].map(to_float)})
            ds_df = ds_df[ds_df["order_no"].astype(bool)].groupby("order_no", as_index=False).sum()
        else:
            ds_df = pd.DataFrame(columns=["order_no", "ds_processing_fee"])
    else:
        ds_df = pd.DataFrame(columns=["order_no", "ds_processing_fee"])

    fine_name = find_sheet(sheets, ["fine", "fines"])
    if fine_name:
        fn = sheets[fine_name]
        c_o = find_column(fn, ["order_sn", "order_no", "order"])
        c_a = find_column(fn, ["fine(KSH)", "fine", "fine_ksh", "fineksh"])
        if c_o and c_a:
            fine_df = pd.DataFrame({"order_no": clean_order_series(fn[c_o]), "fines": fn[c_a].map(to_float)})
            fine_df = fine_df[fine_df["order_no"].astype(bool)].groupby("order_no", as_index=False).sum()
        else:
            fine_df = pd.DataFrame(columns=["order_no", "fines"])
    else:
        fine_df = pd.DataFrame(columns=["order_no", "fines"])

    od_name = find_sheet(sheets, ["Other Deductions", "otherdeductions", "other_ded_columns", "other_deductions"])
    if od_name:
        od = sheets[od_name]
        c_o = find_column(od, ["Order SN", "order_sn", "order_no", "order"])
        c_a = find_column(od, ["Amount（ksh）", "amount(ksh)", "amount", "amount_ksh"])
        if c_o and c_a:
            od_df = pd.DataFrame({"order_no": clean_order_series(od[c_o]), "other_deductions": od[c_a].map(to_float)})
            od_df = od_df[od_df["order_no"].astype(bool)].groupby("order_no", as_index=False).sum()
        else:
            od_df = pd.DataFrame(columns=["order_no", "other_deductions"])
    else:
        od_df = pd.DataFrame(columns=["order_no", "other_deductions"])

    master = bill_df.merge(ds_df, on="order_no", how="left")
    master = master.merge(fine_df, on="order_no", how="left")
    master = master.merge(od_df, on="order_no", how="left")
    for col in ["ds_processing_fee", "fines", "other_deductions", "commission"]:
        master[col] = master[col].fillna(0.0)

    active = read_table("SELECT * FROM active_daily_orders")
    active_map = {row["order_no"]: row for _, row in active.iterrows()}

    matched = 0
    unkeyed = 0
    with get_conn() as conn:
        for _, row in master.iterrows():
            on = row["order_no"]
            complete_amount = float(row["complete_amount"])
            commission = float(row["commission"])
            ds_fee = float(row["ds_processing_fee"])
            fines = float(row["fines"])
            other = float(row["other_deductions"])
            net_payout = complete_amount - abs(commission) - abs(ds_fee) - abs(fines) - abs(other)
            
            statement_shop = normalize_shop_name(row["shop_name"])

            if on in active_map:
                a = active_map[on]
                conn.execute(
                    """
                    INSERT OR REPLACE INTO historical_archive
                        (order_no, shop_name, goods_name, qty, selling_price, settlement_period,
                         complete_amount, commission, ds_processing_fee, fines, other_deductions, net_payout)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        on,
                        normalize_shop_name(a["shop_name"]),
                        a["goods_name"],
                        int(a["qty"]) if a["qty"] is not None else 0,
                        float(a["selling_price"]) if a["selling_price"] is not None else 0.0,
                        settlement_period,
                        complete_amount,
                        commission,
                        ds_fee,
                        fines,
                        other,
                        net_payout,
                    ),
                )
                conn.execute("DELETE FROM active_daily_orders WHERE order_no = ?", (on,))
                conn.execute("DELETE FROM unkeyed_buffer WHERE order_no = ?", (on,))
                matched += 1
            else:
                already = conn.execute("SELECT 1 FROM historical_archive WHERE order_no = ?", (on,)).fetchone()
                if already:
                    continue
                conn.execute(
                    """
                    INSERT OR REPLACE INTO unkeyed_buffer
                        (order_no, shop_name, settlement_period, complete_amount)
                    VALUES (?,?,?,?)
                    """,
                    (on, statement_shop, settlement_period, complete_amount),
                )
                unkeyed += 1

    return {
        "matched": matched,
        "unkeyed": unkeyed,
        "total": len(master),
        "warnings": warnings,
    }


with tab_recon:
    st.subheader("🔄 Weekly Multi-Sheet Matching Engine")
    col1, col2 = st.columns([2, 1])
    period = col1.text_input("Settlement Period Label Reference", value=f"Week of {date.today().isoformat()}")
    settlement_file = col2.file_uploader("Drop settlement sheet document here", type=["xlsx", "xls"], label_visibility="collapsed")

    if settlement_file and st.button("🚀 Run System Settlement Reconciliation", type="primary"):
        with st.spinner("Executing line-by-line validation scripts..."):
            try:
                result = run_reconciliation(settlement_file, period)
                st.success(f"Processed {result['total']} items -> ✅ {result['matched']} matched, ⚠️ {result['unkeyed']} exception items.")
                st.rerun()
            except Exception as exc:
                st.error(f"Reconciliation halted safely: {exc}")

# ---------------------------------------------------------------------------
# MODULE D — Un-keyed Buffer Exception Handler & Deletion Control
# ---------------------------------------------------------------------------
with tab_buffer:
    st.subheader("⚠️ Missing Log Staging Exception Buffer Workspace")
    if buffer_df.empty:
        st.success("🎉 Exception staging containers are clear for this selection view context range.")
    else:
        buffer_seed = buffer_df.copy()
        buffer_seed.insert(0, "🗑️ Select", False)
        
        edited_buffer = st.data_editor(
            buffer_seed, key="buffer_deletion_editor", use_container_width=True, hide_index=True,
            column_config={
                "🗑️ Select": st.column_config.CheckboxColumn("🗑️"),
                "order_no": st.column_config.TextColumn("Order No.", disabled=True),
                "shop_name": st.column_config.TextColumn("Shop Origin Category", disabled=True),
                "settlement_period": st.column_config.TextColumn("Statement ID Label", disabled=True),
                "complete_amount": st.column_config.NumberColumn("Gross Value", format="%.2f", disabled=True),
            }
        )

        b_col1, b_col2, b_col3 = st.columns([1.5, 1.5, 3])
        
        if b_col1.button("♻️ Rematch Staged Rows", type="primary", use_container_width=True):
            rematched = 0
            with get_conn() as conn:
                buf_rows = conn.execute("SELECT * FROM unkeyed_buffer").fetchall()
                for b in buf_rows:
                    a = conn.execute("SELECT * FROM active_daily_orders WHERE order_no = ?", (b["order_no"],)).fetchone()
                    if not a:
                        continue
                    complete_amount = float(b["complete_amount"] or 0)
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO historical_archive
                            (order_no, shop_name, goods_name, qty, selling_price, settlement_period,
                             complete_amount, commission, ds_processing_fee, fines, other_deductions, net_payout)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (b["order_no"], normalize_shop_name(a["shop_name"]), a["goods_name"], int(a["qty"] or 0),
                         float(a["selling_price"] or 0), b["settlement_period"], complete_amount, 0.0, 0.0, 0.0, 0.0, complete_amount),
                    )
                    conn.execute("DELETE FROM active_daily_orders WHERE order_no = ?", (b["order_no"],))
                    conn.execute("DELETE FROM unkeyed_buffer WHERE order_no = ?", (b["order_no"],))
                    rematched += 1
            st.toast(f"Re-mapped {rematched} elements to long-term storage.", icon="✅")
            st.rerun()

        if b_col2.button("🗑️ Delete Selected Exceptions", use_container_width=True):
            buffer_to_del = edited_buffer[edited_buffer["🗑️ Select"].fillna(False)]
            if not buffer_to_del.empty:
                del_ids = buffer_to_del["order_no"].tolist()
                with get_conn() as conn:
                    conn.executemany("DELETE FROM unkeyed_buffer WHERE order_no = ?", [(i,) for i in del_ids])
                st.rerun()

# ---------------------------------------------------------------------------
# MODULE E — Permanent Lifetime Archive View & Deletion Control
# ---------------------------------------------------------------------------
with tab_archive:
    st.subheader("📚 Reconciled Lifetime Database Archive Ledger")
    if archive_df.empty:
        st.info("No compiled settlement matrices recorded inside history metrics yet.")
    else:
        archive_seed = archive_df.copy()
        archive_seed.insert(0, "🗑️ Select", False)
        
        edited_archive = st.data_editor(
            archive_seed, key="archive_deletion_editor", use_container_width=True, hide_index=True,
            column_config={
                "🗑️ Select": st.column_config.CheckboxColumn("🗑️"),
                "order_no": st.column_config.TextColumn("Order No.", disabled=True),
                "shop_name": st.column_config.TextColumn("Shop Category", disabled=True),
                "net_payout": st.column_config.NumberColumn("Net Payout Transferred", format="%.2f", disabled=True),
            }
        )
        
        col_arch_1, col_arch_2, col_arch_3 = st.columns([1.5, 1.5, 1.5])
        if col_arch_1.button("🗑️ Delete Selected Historical Logs", use_container_width=True):
            archive_to_del = edited_archive[edited_archive["🗑️ Select"].fillna(False)]
            if not archive_to_del.empty:
                arch_del_ids = archive_to_del["order_no"].tolist()
                with get_conn() as conn:
                    conn.executemany("DELETE FROM historical_archive WHERE order_no = ?", [(i,) for i in arch_del_ids])
                st.rerun()

# ---------------------------------------------------------------------------
# MODULE F — CODE-FREE STOREFRONT MANAGEMENT SETTINGS TAB
# ---------------------------------------------------------------------------
with tab_settings:
    st.subheader("⚙️ Storefront & Shop Management Panel")
    st.caption("Add, remove, or fix typos in your shop lists without changing code. Changes deploy instantly.")
    
    # 1. Layout splitting form and current items
    set_col1, set_col2 = st.columns([1, 1.5])
    
    with set_col1:
        st.markdown("### ➕ Register New Storefront")
        with st.form("add_shop_form", clear_on_submit=True):
            new_shop_input = st.text_input("Official Shop Name (e.g., EDITECH GLOBAL)", help="Use clear unique system names.")
            new_aliases_input = st.text_input("Keywords / Upload Search Aliases (Comma-separated)", help="Example: EDITECH, EDIT, DIGI")
            submit_shop = st.form_submit_button("💾 Save New Store", type="primary")
            
            if submit_shop:
                ns = new_shop_input.strip().upper()
                if not ns:
                    st.error("Store name cannot remain empty.")
                else:
                    try:
                        with get_conn() as conn:
                            conn.execute("INSERT INTO managed_shops (shop_name, aliases) VALUES (?, ?)", (ns, new_aliases_input.strip()))
                        st.success(f"Registered '{ns}' into active configurations.")
                        st.rerun()
                    except sqlite3.IntegrityError:
                        st.error("A shop with that identical name configuration already exists.")

    with set_col2:
        st.markdown("### 📝 Existing Registered Stores & Mapping Fixes")
        shops_data = read_table("SELECT id, shop_name, aliases FROM managed_shops ORDER BY shop_name")
        shops_data.insert(0, "🗑️ Delete", False)
        
        edited_shops = st.data_editor(
            shops_data,
            key="shops_settings_editor",
            use_container_width=True,
            hide_index=True,
            column_config={
                "🗑️ Delete": st.column_config.CheckboxColumn("🗑️", help="Permanently drop this shop profile"),
                "id": st.column_config.TextColumn("System Key ID", disabled=True),
                "shop_name": st.column_config.TextColumn("Shop Name Designation", required=True),
                "aliases": st.column_config.TextColumn("Parsing Search Aliases Tokens"),
            }
        )
        
        if st.button("Apply Settings Adjustments & Migrations", type="secondary"):
            with get_conn() as conn:
                # Loop through changes to apply renaming updates or deletions
                for _, row in edited_shops.iterrows():
                    orig_row = [r for r in shops_data.to_dict('records') if r['id'] == row['id']][0]
                    old_name = orig_row['shop_name']
                    new_name = str(row['shop_name']).strip().upper()
                    new_aliases = str(row['aliases'] or '').strip()
                    
                    # Handle complete storefront absolute removal
                    if row["🗑️ Delete"]:
                        conn.execute("DELETE FROM managed_shops WHERE id = ?", (row["id"],))
                        st.toast(f"Removed storefront mapping configuration container for '{old_name}'", icon="🗑️")
                        continue
                    
                    # Handle renaming migration logic across ALL relational sheets automatically
                    if old_name != new_name:
                        conn.execute("UPDATE managed_shops SET shop_name = ?, aliases = ? WHERE id = ?", (new_name, new_aliases, row["id"]))
                        conn.execute("UPDATE active_daily_orders SET shop_name = ? WHERE shop_name = ?", (new_name, old_name))
                        conn.execute("UPDATE unkeyed_buffer SET shop_name = ? WHERE shop_name = ?", (new_name, old_name))
                        conn.execute("UPDATE historical_archive SET shop_name = ? WHERE shop_name = ?", (new_name, old_name))
                        st.success(f"Migrated data tables: Changed '{old_name}' to '{new_name}' safely.")
                    else:
                        # Simple alias string update
                        conn.execute("UPDATE managed_shops SET aliases = ? WHERE id = ?", (new_aliases, row["id"]))
            st.rerun()

st.divider()
st.caption(f"Database Connection: `{DB_FILE}` · Live Sync: {datetime.now():%Y-%m-%d %H:%M:%S}")
