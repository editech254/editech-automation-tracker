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
        
        # Check if shop_name column exists in active_daily_orders, if not, create table or alter
        c.execute("PRAGMA table_info(active_daily_orders)")
        columns = [row["name"] for row in c.fetchall()]
        if not columns:
            c.execute(
                """
                CREATE TABLE active_daily_orders (
                    date TEXT,
                    order_no TEXT PRIMARY KEY,
                    shop_name TEXT DEFAULT 'Default Shop',
                    goods_name TEXT,
                    qty INTEGER,
                    selling_price REAL
                )
                """
            )
        elif "shop_name" not in columns:
            c.execute("ALTER TABLE active_daily_orders ADD COLUMN shop_name TEXT DEFAULT 'Default Shop'")

        # Check unkeyed_buffer columns
        c.execute("PRAGMA table_info(unkeyed_buffer)")
        columns = [row["name"] for row in c.fetchall()]
        if not columns:
            c.execute(
                """
                CREATE TABLE unkeyed_buffer (
                    order_no TEXT PRIMARY KEY,
                    shop_name TEXT DEFAULT 'Default Shop',
                    settlement_period TEXT,
                    complete_amount REAL,
                    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        elif "shop_name" not in columns:
            c.execute("ALTER TABLE unkeyed_buffer ADD COLUMN shop_name TEXT DEFAULT 'Default Shop'")

        # Check historical_archive columns
        c.execute("PRAGMA table_info(historical_archive)")
        columns = [row["name"] for row in c.fetchall()]
        if not columns:
            c.execute(
                """
                CREATE TABLE historical_archive (
                    order_no TEXT PRIMARY KEY,
                    shop_name TEXT DEFAULT 'Default Shop',
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
            c.execute("ALTER TABLE historical_archive ADD COLUMN shop_name TEXT DEFAULT 'Default Shop'")


init_db()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_DIGIT_RE = re.compile(r"\D+")


def clean_order_no(value) -> str:
    """Strip all non-digit characters from Kilimall order identifiers."""
    if value is None:
        return ""
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return ""
    return _DIGIT_RE.sub("", s)


def clean_order_series(series: pd.Series) -> pd.Series:
    return series.map(clean_order_no)


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
    """Case/space/punct insensitive column lookup."""
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
# SIDEBAR — Shop Customization & Global Filtering
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("🏪 Shop Management")
    
    # Extract unique shop variants currently recorded inside the platform
    with get_conn() as conn:
        shops_active = [r["shop_name"] for r in conn.execute("SELECT DISTINCT shop_name FROM active_daily_orders").fetchall()]
        shops_archive = [r["shop_name"] for r in conn.execute("SELECT DISTINCT shop_name FROM historical_archive").fetchall()]
        shops_buffer = [r["shop_name"] for r in conn.execute("SELECT DISTINCT shop_name FROM unkeyed_buffer").fetchall()]
    
    all_known_shops = sorted(list(set(shops_active + shops_archive + shops_buffer + ["Default Shop"])))
    
    selected_shop = st.selectbox(
        "🎯 Filter Views by Shop Name",
        options=["All Shops"] + all_known_shops,
        index=0,
        help="Filters metrics and data tables. Reconciliations preserve specific store attachments dynamically."
    )

# ---------------------------------------------------------------------------
# Data Fetching and Global Filtering Rules
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
# Header
# ---------------------------------------------------------------------------
st.title("📦 Kilimall Reconciliation Suite")
st.caption(
    f"EDITECH DIGITAL — daily order capture, weekly settlement reconciliation, "
    f"exception handling & lifetime business intelligence. Currently viewing: **{selected_shop}**"
)

# ---------------------------------------------------------------------------
# MODULE A — Live Business Performance Scorecard
# ---------------------------------------------------------------------------
total_sold = float(active_df["selling_price"].fillna(0).sum()) + float(
    archive_df["selling_price"].fillna(0).sum()
)
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

st.subheader("📊 Lifetime Business Scorecard")
m1, m2, m3, m4 = st.columns(4)
m1.metric("💰 Total Value Sold", ksh(total_sold))
m2.metric("🏦 Total Net Paid Out", ksh(total_net_paid))
m3.metric("✂️ Total Fees & Deductions", ksh(total_fees))
m4.metric("⏳ Pending Payment", ksh(pending_payment), f"{len(active_df)} orders")

st.divider()

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tab_ledger, tab_recon, tab_buffer, tab_archive = st.tabs(
    [
        "📝 Daily Ledger",
        "🔄 Reconcile Settlement",
        f"⚠️ Un-keyed Buffer ({len(buffer_df)})",
        f"📚 Lifetime Archive ({len(archive_df)})",
    ]
)

# ---------------------------------------------------------------------------
# MODULE B — Interactive Grid Ledger
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
            str(r.get("shop_name") or "Default Shop").strip(),
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
            # If viewing a single shop, only replace rows belonging to that shop
            conn.execute("DELETE FROM active_daily_orders WHERE shop_name = ?", (selected_shop,))
            shop_rows = [r for r in rows if r[2] == selected_shop]
            conn.executemany(
                "INSERT INTO active_daily_orders (date, order_no, shop_name, goods_name, qty, selling_price) VALUES (?,?,?,?,?,?)",
                shop_rows,
            )
            
    return len(rows)


with tab_ledger:
    st.subheader("📝 Active Daily Orders Ledger")
    st.caption(
        "Manage daily order inputs below. Assign precise values to **Shop Name** to split metrics. "
        "Tick the **🗑️ (Delete)** column check-box and click **Delete Selected Orders** to clear mistakes."
    )

    # --- Upload daily orders ----------------------------------------------
    with st.expander("📤 Bulk Import Daily Orders (Excel / CSV)", expanded=False):
        st.caption(
            "Auto-detected headers: **date, order_no, shop_name, goods_name, qty, selling_price**. "
            "Rows are appended safely. If **shop_name** is missing, fallback uses your manual field choice."
        )
        up_col1, up_col2, up_col3 = st.columns([2, 1, 1])
        upload_file = up_col1.file_uploader(
            "Orders file", type=["xlsx", "xls", "csv"], label_visibility="collapsed", key="orders_upload"
        )
        fallback_shop = up_col2.text_input("Fallback Shop Name", value="Default Shop")
        replace_mode = up_col3.checkbox("Wipe Current Filter Range", value=False)

        if upload_file and st.button("⬆️ Process Data Import", type="primary"):
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
                    st.error("Missing mandatory system Order Number row link parameter inside document.")
                else:
                    norm = pd.DataFrame({
                        "date": udf[c_date] if c_date else date.today().isoformat(),
                        "order_no": clean_order_series(udf[c_ord]),
                        "shop_name": udf[c_shop].fillna(fallback_shop).strip() if c_shop else fallback_shop,
                        "goods_name": udf[c_goods] if c_goods else "",
                        "qty": udf[c_qty].map(lambda v: int(to_float(v))) if c_qty else 1,
                        "selling_price": udf[c_price].map(to_float) if c_price else 0.0,
                    })
                    norm = norm[norm["order_no"].astype(bool)]

                    _snapshot_active()
                    
                    full_current = read_table("SELECT date, order_no, shop_name, goods_name, qty, selling_price FROM active_daily_orders")
                    if replace_mode:
                        if selected_shop == "All Shops":
                            merged = norm
                        else:
                            other_shops = full_current[full_current["shop_name"] != selected_shop]
                            merged = pd.concat([other_shops, norm], ignore_index=True)
                    else:
                        merged = pd.concat([full_current, norm], ignore_index=True)
                        
                    n = _replace_active(merged)
                    st.toast(f"Successfully processed {len(norm)} rows inside workspace.", icon="✅")
                    st.rerun()
            except Exception as exc:
                st.error(f"Import failed: {exc}")

    # --- Editable grid ----------------------------------------------------
    grid_seed = active_df.copy()
    if grid_seed.empty:
        grid_seed = pd.DataFrame(
            [{"date": date.today().isoformat(), "order_no": "", "shop_name": selected_shop if selected_shop != "All Shops" else "Default Shop", "goods_name": "", "qty": 1, "selling_price": 0.0}]
        )
    else:
        grid_seed = grid_seed[["date", "order_no", "shop_name", "goods_name", "qty", "selling_price"]]
    grid_seed.insert(0, "_delete", False)

    edited = st.data_editor(
        grid_seed,
        key="ledger_editor",
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "_delete": st.column_config.CheckboxColumn("🗑️", help="Select lines to purge", default=False),
            "date": st.column_config.TextColumn("Date"),
            "order_no": st.column_config.TextColumn("Order No.", required=True),
            "shop_name": st.column_config.TextColumn("Shop Name", required=True),
            "goods_name": st.column_config.TextColumn("Goods Name"),
            "qty": st.column_config.NumberColumn("Qty", min_value=0, step=1),
            "selling_price": st.column_config.NumberColumn("Selling Price", min_value=0.0, format="%.2f"),
        },
    )

    undo_stack = st.session_state.get("undo_stack", [])
    col_a, col_b, col_c, _ = st.columns([1.5, 1.5, 1, 3])

    if col_a.button("💾 Commit Ledger Updates", type="primary", use_container_width=True):
        try:
            _snapshot_active()
            keep = edited[~edited["_delete"].fillna(False)].drop(columns=["_delete"])
            
            # Reinsert rows belonging to other shops if viewing via localized shop view boundaries
            if selected_shop != "All Shops":
                full_current = read_table("SELECT date, order_no, shop_name, goods_name, qty, selling_price FROM active_daily_orders")
                other_shops = full_current[full_current["shop_name"] != selected_shop]
                keep = pd.concat([other_shops, keep], ignore_index=True)
                
            _replace_active(keep)
            st.toast("Active Daily Database Ledger committed successfully.", icon="✅")
            st.rerun()
        except Exception as exc:
            st.error(f"Failed to commit database modifications: {exc}")

    if col_b.button("🗑️ Delete Selected Orders", use_container_width=True):
        to_del = edited[edited["_delete"].fillna(False)]
        if to_del.empty:
            st.toast("No configuration modifications marked for removal.", icon="ℹ️")
        else:
            _snapshot_active()
            ids = [clean_order_no(x) for x in to_del["order_no"].tolist() if clean_order_no(x)]
            with get_conn() as conn:
                conn.executemany("DELETE FROM active_daily_orders WHERE order_no = ?", [(i,) for i in ids])
            st.toast(f"Deleted {len(ids)} item profiles from database context.", icon="🗑️")
            st.rerun()

    if col_c.button(f"↩️ Undo Rollback ({len(undo_stack)})", use_container_width=True, disabled=not undo_stack):
        prev = st.session_state["undo_stack"].pop()
        _replace_active(prev)
        st.toast("Reverted last local modification frame state.", icon="↩️")
        st.rerun()

# ---------------------------------------------------------------------------
# MODULE C — Multi-sheet Reconciliation Engine
# ---------------------------------------------------------------------------
def run_reconciliation(file, settlement_period: str) -> dict:
    sheets = pd.read_excel(file, sheet_name=None, dtype=str)
    warnings: list[str] = []

    # --- bill details ---
    bill_name = find_sheet(sheets, ["bill details", "billdetails", "bill_details", "bill detail"])
    if not bill_name:
        raise ValueError("Required primary matching verification sheet matrix 'bill details' not identified.")
    bill = sheets[bill_name].copy()

    col_order = find_column(bill, ["order_sn", "order_no", "order sn", "order number"])
    col_amount = find_column(bill, ["complete amount", "completeamount", "complete_amount", "amount"])
    col_comm = find_column(bill, ["Commission", "commission"])
    col_settle = find_column(bill, ["settlement", "settle"])
    col_store_bill = find_column(bill, ["store_name", "storeName", "store", "shop_name"])
    
    if not col_order or not col_amount:
        raise ValueError("'bill details' sheet must contain structured order identify elements.")

    bill_df = pd.DataFrame(
        {
            "order_no": clean_order_series(bill[col_order]),
            "complete_amount": bill[col_amount].map(to_float),
            "commission": bill[col_comm].map(to_float) if col_comm else 0.0,
            "settlement_base": bill[col_settle].map(to_float) if col_settle else 0.0,
            "shop_name": bill[col_store_bill].fillna("Default Shop").strip() if col_store_bill else "Default Shop",
        }
    )
    bill_df = bill_df[bill_df["order_no"].astype(bool)]
    bill_df = bill_df.groupby("order_no", as_index=False).agg(
        {"complete_amount": "sum", "commission": "sum", "settlement_base": "sum", "shop_name": "first"}
    )

    # --- ds processing fee ---
    ds_name = find_sheet(sheets, ["ds processing fee", "dsprocessingfee", "ds_processing_fee"])
    if ds_name:
        ds = sheets[ds_name]
        c_o = find_column(ds, ["order_no", "order_sn", "order"])
        c_a = find_column(ds, ["amout", "amount"])
        if c_o and c_a:
            ds_df = pd.DataFrame({"order_no": clean_order_series(ds[c_o]), "ds_processing_fee": ds[c_a].map(to_float)})
            ds_df = ds_df[ds_df["order_no"].astype(bool)].groupby("order_no", as_index=False).sum()
        else:
            warnings.append("'ds processing fee' structure missing critical validation links.")
            ds_df = pd.DataFrame(columns=["order_no", "ds_processing_fee"])
    else:
        ds_df = pd.DataFrame(columns=["order_no", "ds_processing_fee"])

    # --- fine ---
    fine_name = find_sheet(sheets, ["fine", "fines"])
    if fine_name:
        fn = sheets[fine_name]
        c_o = find_column(fn, ["order_sn", "order_no", "order"])
        c_a = find_column(fn, ["fine(KSH)", "fine", "fine_ksh", "fineksh"])
        if c_o and c_a:
            fine_df = pd.DataFrame({"order_no": clean_order_series(fn[c_o]), "fines": fn[c_a].map(to_float)})
            fine_df = fine_df[fine_df["order_no"].astype(bool)].groupby("order_no", as_index=False).sum()
        else:
            warnings.append("'fine' panel document structure mismatch parameters.")
            fine_df = pd.DataFrame(columns=["order_no", "fines"])
    else:
        fine_df = pd.DataFrame(columns=["order_no", "fines"])

    # --- Other Deductions ---
    od_name = find_sheet(sheets, ["Other Deductions", "otherdeductions", "other_deductions"])
    if od_name:
        od = sheets[od_name]
        c_o = find_column(od, ["Order SN", "order_sn", "order_no", "order"])
        c_a = find_column(od, ["Amount（ksh）", "amount(ksh)", "amount", "amount_ksh"])
        if c_o and c_a:
            od_df = pd.DataFrame({"order_no": clean_order_series(od[c_o]), "other_deductions": od[c_a].map(to_float)})
            od_df = od_df[od_df["order_no"].astype(bool)].groupby("order_no", as_index=False).sum()
        else:
            warnings.append("'Other Deductions' validation maps missed tracking parameters.")
            od_df = pd.DataFrame(columns=["order_no", "other_deductions"])
    else:
        od_df = pd.DataFrame(columns=["order_no", "other_deductions"])

    # --- Merge all settlement data ---
    master = bill_df.merge(ds_df, on="order_no", how="left")
    master = master.merge(fine_df, on="order_no", how="left")
    master = master.merge(od_df, on="order_no", how="left")
    for col in ["ds_processing_fee", "fines", "other_deductions", "commission"]:
        master[col] = master[col].fillna(0.0)

    # --- Match against active_daily_orders ---
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
            
            # Extract Kilimall statement sheet shop attribution fallback
            statement_shop = str(row["shop_name"]).strip()

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
                        a["shop_name"] if (a["shop_name"] and a["shop_name"] != "Default Shop") else statement_shop,
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
    st.subheader("🔄 Weekly Settlement Reconciliation Engine")
    st.caption(
        "Upload a multi-tab statement workbook. The engine maps platform metrics, parses shop indicators "
        "automatically, and completes reconciliation updates against your local inventory ledger maps."
    )

    col1, col2 = st.columns([2, 1])
    period = col1.text_input(
        "Settlement Period Label",
        value=f"Week of {date.today().isoformat()}",
    )
    settlement_file = col2.file_uploader("Settlement Workbook Document", type=["xlsx", "xls"], label_visibility="collapsed")

    if settlement_file and st.button("🚀 Execute System Match Logic", type="primary"):
        try:
            result = run_reconciliation(settlement_file, period)
            for w in result["warnings"]:
                st.toast(w, icon="⚠️")
            st.success(
                f"Completed platform statements audit summary execution profile -> "
                f"✅ {result['matched']} matched files safely moved to deep storage archive, ⚠️ {result['unkeyed']} flagged exceptions assigned to staging buffer tracking views."
            )
            st.rerun()
        except ValueError as exc:
            st.error(f"Reconciliation halted safely: {exc}")
        except Exception as exc:
            st.error(f"System validation exception encountered: {exc}")

# ---------------------------------------------------------------------------
# MODULE D — Un-keyed Buffer Exception Handler & Deletion Control
# ---------------------------------------------------------------------------
with tab_buffer:
    st.subheader("⚠️ Flagged Exceptions Staging Area (Un-keyed Payout Elements)")
    st.caption(
        "Orders tracked here were processed on Kilimall but are completely missing from your local history logs. "
        "To clear exceptions, record these rows in the **Daily Ledger**, hit save, and select **Rematch Staged Rows** below. "
        "Alternatively, select rows and use the deletion tools to drop anomalies."
    )

    if buffer_df.empty:
        st.success("🎉 Exception staging container is empty. All logs match historical entries perfectly.")
    else:
        # Create an editable interface allowing interactive line-item removals directly out of the exception buffer
        buffer_seed = buffer_df.copy()
        buffer_seed.insert(0, "🗑️ Select", False)
        
        edited_buffer = st.data_editor(
            buffer_seed,
            key="buffer_deletion_editor",
            use_container_width=True,
            hide_index=True,
            column_config={
                "🗑️ Select": st.column_config.CheckboxColumn("🗑️", help="Tick items to purge from staging buffer"),
                "order_no": st.column_config.TextColumn("Order No.", disabled=True),
                "shop_name": st.column_config.TextColumn("Shop Category", disabled=True),
                "settlement_period": st.column_config.TextColumn("Statement Marker", disabled=True),
                "complete_amount": st.column_config.NumberColumn("Dispatched Gross Value", format="%.2f", disabled=True),
                "detected_at": st.column_config.TextColumn("Staging Entry Date", disabled=True)
            }
        )

        b_col1, b_col2, b_col3 = st.columns([1.5, 1.5, 3])
        
        if b_col1.button("♻️ Rematch Staged Rows", type="primary", use_container_width=True):
            rematched = 0
            with get_conn() as conn:
                buf_rows = conn.execute("SELECT * FROM unkeyed_buffer").fetchall()
                for b in buf_rows:
                    a = conn.execute(
                        "SELECT * FROM active_daily_orders WHERE order_no = ?", (b["order_no"],)
                    ).fetchone()
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
                        (
                            b["order_no"],
                            a["shop_name"] if a["shop_name"] else b["shop_name"],
                            a["goods_name"],
                            int(a["qty"] or 0),
                            float(a["selling_price"] or 0),
                            b["settlement_period"],
                            complete_amount,
                            0.0, 0.0, 0.0, 0.0,
                            complete_amount,
                        ),
                    )
                    conn.execute("DELETE FROM active_daily_orders WHERE order_no = ?", (b["order_no"],))
                    conn.execute("DELETE FROM unkeyed_buffer WHERE order_no = ?", (b["order_no"],))
                    rematched += 1
            st.toast(f"Re-mapped {rematched} elements to long-term database storage.", icon="✅")
            st.rerun()

        if b_col2.button("🗑️ Delete Selected Exceptions", use_container_width=True):
            buffer_to_del = edited_buffer[edited_buffer["🗑️ Select"].fillna(False)]
            if buffer_to_del.empty:
                st.toast("No staging items marked for elimination.", icon="ℹ️")
            else:
                del_ids = buffer_to_del["order_no"].tolist()
                with get_conn() as conn:
                    conn.executemany("DELETE FROM unkeyed_buffer WHERE order_no = ?", [(i,) for i in del_ids])
                st.toast(f"Purged {len(del_ids)} rows from validation buffer containers.", icon="🗑️")
                st.rerun()

        if b_col3.button("🧹 Wipe Staging Area Completely", use_container_width=True):
            with get_conn() as conn:
                if selected_shop == "All Shops":
                    conn.execute("DELETE FROM unkeyed_buffer")
                else:
                    conn.execute("DELETE FROM unkeyed_buffer WHERE shop_name = ?", (selected_shop,))
            st.toast("Staging view reset successfully.", icon="🧹")
            st.rerun()

# ---------------------------------------------------------------------------
# MODULE E — Lifetime Archive View & Administrative Deletion Panel
# ---------------------------------------------------------------------------
with tab_archive:
    st.subheader("📚 Lifetime Long-term Database Archive & Auditing Console")
    
    if archive_df.empty:
        st.info("No reconciled billing matrices recorded inside archiving profiles yet.")
    else:
        st.caption(
            "Reviewing cumulative finalized transaction points below. "
            "To reverse an archived match or fix duplicate system elements, check the lines and press **Delete Selected Historical Logs**."
        )
        
        archive_seed = archive_df.copy()
        archive_seed.insert(0, "🗑️ Select", False)
        
        edited_archive = st.data_editor(
            archive_seed,
            key="archive_deletion_editor",
            use_container_width=True,
            hide_index=True,
            column_config={
                "🗑️ Select": st.column_config.CheckboxColumn("🗑️", help="Tick records to permanently delete from tracking archive"),
                "order_no": st.column_config.TextColumn("Order No.", disabled=True),
                "shop_name": st.column_config.TextColumn("Shop Category", disabled=True),
                "goods_name": st.column_config.TextColumn("Product Name", disabled=True),
                "qty": st.column_config.NumberColumn("Quantity", disabled=True),
                "selling_price": st.column_config.NumberColumn("Expected Price", disabled=True),
                "settlement_period": st.column_config.TextColumn("Statement Reference", disabled=True),
                "complete_amount": st.column_config.NumberColumn("Gross Received", disabled=True),
                "commission": st.column_config.NumberColumn("Commission Out", disabled=True),
                "ds_processing_fee": st.column_config.NumberColumn("Processing Costs", disabled=True),
                "fines": st.column_config.NumberColumn("Penalties", disabled=True),
                "other_deductions": st.column_config.NumberColumn("Misc. Adjustments", disabled=True),
                "net_payout": st.column_config.NumberColumn("Net Payout Delivered", disabled=True),
                "archived_at": st.column_config.TextColumn("Archived Date Time Stamp", disabled=True)
            }
        )
        
        col_arch_1, col_arch_2, col_arch_3 = st.columns([1.5, 1.5, 1.5])
        
        if col_arch_1.button("🗑️ Delete Selected Historical Logs", use_container_width=True):
            archive_to_del = edited_archive[edited_archive["🗑️ Select"].fillna(False)]
            if archive_to_del.empty:
                st.toast("No records selected for deletion in archive viewport.", icon="ℹ️")
            else:
                arch_del_ids = archive_to_del["order_no"].tolist()
                with get_conn() as conn:
                    conn.executemany("DELETE FROM historical_archive WHERE order_no = ?", [(i,) for i in arch_del_ids])
                st.toast(f"Permanently dropped {len(arch_del_ids)} logs from data files. Scorecard metrics updated.", icon="💥")
                st.rerun()

        # Download vectors
        csv = archive_df.to_csv(index=False).encode("utf-8")
        col_arch_2.download_button(
            "⬇️ Export Consolidated Flat CSV File",
            csv,
            file_name=f"kilimall_archive_report_{date.today().isoformat()}.csv",
            mime="text/csv",
            use_container_width=True,
        )
        
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            archive_df.to_excel(w, index=False, sheet_name="Archive")
        col_arch_3.download_button(
            "⬇️ Export Consolidated Formatted Workbook",
            buf.getvalue(),
            file_name=f"kilimall_archive_report_{date.today().isoformat()}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

st.divider()
st.caption(
    f"Database Endpoint Workspace: `{DB_FILE}` · Filter View Count Active Ledger: {len(active_df)} · Exception Staging Buffer: {len(buffer_df)} · "
    f"Archived Ledger Entries: {len(archive_df)} · Core Sync Execution Date: {datetime.now():%Y-%m-%d %H:%M:%S}"
)
