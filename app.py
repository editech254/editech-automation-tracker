"""
EDITECH DIGITAL — Kilimall Order Lifecycle, Reconciliation & BI Software
========================================================================
A state-driven Streamlit application backed by SQLite for managing the
full Kilimall order lifecycle: daily order capture, weekly settlement
reconciliation, exception handling, and lifetime business intelligence.
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
    initial_sidebar_state="collapsed",
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
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS active_daily_orders (
                date TEXT,
                order_no TEXT PRIMARY KEY,
                goods_name TEXT,
                qty INTEGER,
                selling_price REAL
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS unkeyed_buffer (
                order_no TEXT PRIMARY KEY,
                settlement_period TEXT,
                complete_amount REAL,
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS historical_archive (
                order_no TEXT PRIMARY KEY,
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


init_db()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_DIGIT_RE = re.compile(r"\D+")


def clean_order_no(value) -> str:
    """Strip all non-digit characters from Kilimall order identifiers.

    Kilimall exports order numbers wrapped in stray quotes and commas
    (e.g. `",605025391515088"`); we isolate the raw numeric string.
    """
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
# Header
# ---------------------------------------------------------------------------
st.title("📦 Kilimall Reconciliation Suite")
st.caption(
    "EDITECH DIGITAL — daily order capture, weekly settlement reconciliation, "
    "exception handling & lifetime business intelligence."
)

# ---------------------------------------------------------------------------
# MODULE A — Live Business Performance Scorecard
# ---------------------------------------------------------------------------
active_df = read_table("SELECT * FROM active_daily_orders ORDER BY date DESC")
archive_df = read_table("SELECT * FROM historical_archive ORDER BY archived_at DESC")
buffer_df = read_table("SELECT * FROM unkeyed_buffer ORDER BY detected_at DESC")

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
        "📚 Lifetime Archive",
    ]
)

# ---------------------------------------------------------------------------
# MODULE B — Interactive Grid Ledger
# ---------------------------------------------------------------------------
with tab_ledger:
    st.subheader("📝 Active Daily Orders — Interactive Grid")
    st.caption(
        "Paste rows directly from Excel, click **+** to add, or delete rows. "
        "Hit **Commit Grid Changes** to persist."
    )

    grid_seed = active_df.copy()
    if grid_seed.empty:
        grid_seed = pd.DataFrame(
            [{"date": date.today().isoformat(), "order_no": "", "goods_name": "", "qty": 1, "selling_price": 0.0}]
        )
    else:
        grid_seed = grid_seed[["date", "order_no", "goods_name", "qty", "selling_price"]]

    edited = st.data_editor(
        grid_seed,
        key="ledger_editor",
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "date": st.column_config.TextColumn("Date", help="YYYY-MM-DD"),
            "order_no": st.column_config.TextColumn("Order No.", required=True),
            "goods_name": st.column_config.TextColumn("Goods Name"),
            "qty": st.column_config.NumberColumn("Qty", min_value=0, step=1),
            "selling_price": st.column_config.NumberColumn("Selling Price (KSH)", min_value=0.0, format="%.2f"),
        },
    )

    col_a, col_b = st.columns([1, 5])
    if col_a.button("💾 Commit Grid Changes", type="primary", use_container_width=True):
        try:
            clean = edited.copy()
            clean["order_no"] = clean_order_series(clean["order_no"])
            clean = clean[clean["order_no"].astype(bool)]
            clean = clean.drop_duplicates(subset=["order_no"], keep="last")

            with get_conn() as conn:
                # Replace-in-place: delete existing active rows, then insert current grid.
                conn.execute("DELETE FROM active_daily_orders")
                rows = [
                    (
                        str(r.get("date") or date.today().isoformat()),
                        str(r["order_no"]),
                        str(r.get("goods_name") or ""),
                        int(r["qty"]) if pd.notna(r.get("qty")) else 0,
                        float(r["selling_price"]) if pd.notna(r.get("selling_price")) else 0.0,
                    )
                    for _, r in clean.iterrows()
                ]
                conn.executemany(
                    "INSERT INTO active_daily_orders (date, order_no, goods_name, qty, selling_price) VALUES (?,?,?,?,?)",
                    rows,
                )
            st.toast(f"Saved {len(rows)} active orders.", icon="✅")
            st.rerun()
        except Exception as exc:
            st.error(f"Failed to commit grid: {exc}")

# ---------------------------------------------------------------------------
# MODULE C — Multi-sheet Reconciliation Engine
# ---------------------------------------------------------------------------
def run_reconciliation(file, settlement_period: str) -> dict:
    """Parse the multi-sheet Kilimall settlement workbook and reconcile."""
    sheets = pd.read_excel(file, sheet_name=None, dtype=str)

    warnings: list[str] = []

    # --- bill details (required) ---
    bill_name = find_sheet(sheets, ["bill details", "billdetails", "bill_details", "bill detail"])
    if not bill_name:
        raise ValueError("Required sheet 'bill details' not found.")
    bill = sheets[bill_name].copy()

    col_order = find_column(bill, ["order_sn", "order_no", "order sn", "order number"])
    col_amount = find_column(bill, ["complete amount", "completeamount", "complete_amount", "amount"])
    col_comm = find_column(bill, ["Commission", "commission"])
    col_settle = find_column(bill, ["settlement", "settle"])
    if not col_order or not col_amount:
        raise ValueError("'bill details' must contain order and complete amount columns.")

    bill_df = pd.DataFrame(
        {
            "order_no": clean_order_series(bill[col_order]),
            "complete_amount": bill[col_amount].map(to_float),
            "commission": bill[col_comm].map(to_float) if col_comm else 0.0,
            "settlement_base": bill[col_settle].map(to_float) if col_settle else 0.0,
        }
    )
    bill_df = bill_df[bill_df["order_no"].astype(bool)]
    bill_df = bill_df.groupby("order_no", as_index=False).agg(
        {"complete_amount": "sum", "commission": "sum", "settlement_base": "sum"}
    )

    # --- ds processing fee (optional) ---
    ds_name = find_sheet(sheets, ["ds processing fee", "dsprocessingfee", "ds_processing_fee"])
    if ds_name:
        ds = sheets[ds_name]
        c_o = find_column(ds, ["order_no", "order_sn", "order"])
        c_a = find_column(ds, ["amout", "amount"])
        if c_o and c_a:
            ds_df = pd.DataFrame(
                {"order_no": clean_order_series(ds[c_o]), "ds_processing_fee": ds[c_a].map(to_float)}
            )
            ds_df = ds_df[ds_df["order_no"].astype(bool)].groupby("order_no", as_index=False).sum()
        else:
            warnings.append("'ds processing fee' sheet missing required columns — skipped.")
            ds_df = pd.DataFrame(columns=["order_no", "ds_processing_fee"])
    else:
        ds_df = pd.DataFrame(columns=["order_no", "ds_processing_fee"])

    # --- fine (optional) ---
    fine_name = find_sheet(sheets, ["fine", "fines"])
    if fine_name:
        fn = sheets[fine_name]
        c_o = find_column(fn, ["order_sn", "order_no", "order"])
        c_a = find_column(fn, ["fine(KSH)", "fine", "fine_ksh", "fineksh"])
        if c_o and c_a:
            fine_df = pd.DataFrame(
                {"order_no": clean_order_series(fn[c_o]), "fines": fn[c_a].map(to_float)}
            )
            fine_df = fine_df[fine_df["order_no"].astype(bool)].groupby("order_no", as_index=False).sum()
        else:
            warnings.append("'fine' sheet missing required columns — skipped.")
            fine_df = pd.DataFrame(columns=["order_no", "fines"])
    else:
        fine_df = pd.DataFrame(columns=["order_no", "fines"])

    # --- Other Deductions (optional) ---
    od_name = find_sheet(sheets, ["Other Deductions", "otherdeductions", "other_deductions"])
    if od_name:
        od = sheets[od_name]
        c_o = find_column(od, ["Order SN", "order_sn", "order_no", "order"])
        c_a = find_column(od, ["Amount（ksh）", "amount(ksh)", "amount", "amount_ksh"])
        if c_o and c_a:
            od_df = pd.DataFrame(
                {"order_no": clean_order_series(od[c_o]), "other_deductions": od[c_a].map(to_float)}
            )
            od_df = od_df[od_df["order_no"].astype(bool)].groupby("order_no", as_index=False).sum()
        else:
            warnings.append("'Other Deductions' sheet missing required columns — skipped.")
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

            if on in active_map:
                a = active_map[on]
                conn.execute(
                    """
                    INSERT OR REPLACE INTO historical_archive
                        (order_no, goods_name, qty, selling_price, settlement_period,
                         complete_amount, commission, ds_processing_fee, fines, other_deductions, net_payout)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        on,
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
                # Clear from buffer if previously flagged
                conn.execute("DELETE FROM unkeyed_buffer WHERE order_no = ?", (on,))
                matched += 1
            else:
                # Skip if already in archive (already settled)
                already = conn.execute(
                    "SELECT 1 FROM historical_archive WHERE order_no = ?", (on,)
                ).fetchone()
                if already:
                    continue
                conn.execute(
                    """
                    INSERT OR REPLACE INTO unkeyed_buffer
                        (order_no, settlement_period, complete_amount)
                    VALUES (?,?,?)
                    """,
                    (on, settlement_period, complete_amount),
                )
                unkeyed += 1

    return {
        "matched": matched,
        "unkeyed": unkeyed,
        "total": len(master),
        "warnings": warnings,
    }


with tab_recon:
    st.subheader("🔄 Weekly Settlement Reconciliation")
    st.caption(
        "Upload the multi-sheet Kilimall settlement Excel. We auto-clean order IDs, "
        "aggregate all fee sheets, and reconcile against your active daily ledger."
    )

    col1, col2 = st.columns([2, 1])
    period = col1.text_input(
        "Settlement Period Label",
        value=f"Week of {date.today().isoformat()}",
        help="Free-form label saved with each reconciled order.",
    )
    settlement_file = col2.file_uploader("Settlement file", type=["xlsx", "xls"], label_visibility="collapsed")

    if settlement_file and st.button("🚀 Run Reconciliation", type="primary"):
        try:
            result = run_reconciliation(settlement_file, period)
            for w in result["warnings"]:
                st.toast(w, icon="⚠️")
            st.success(
                f"Processed {result['total']} settled orders → "
                f"✅ {result['matched']} matched & archived, ⚠️ {result['unkeyed']} sent to un-keyed buffer."
            )
            st.rerun()
        except ValueError as exc:
            st.error(f"Reconciliation aborted: {exc}")
        except Exception as exc:
            st.error(f"Unexpected error while reconciling: {exc}")

# ---------------------------------------------------------------------------
# MODULE D — Un-keyed Buffer Exception Handler
# ---------------------------------------------------------------------------
with tab_buffer:
    st.subheader("⚠️ Un-keyed Buffer — Exceptions Awaiting Reconciliation")
    st.caption(
        "These orders appeared on Kilimall's settlement but were missing from your daily ledger. "
        "Key them into the **Daily Ledger** tab, commit, then click **Rematch Buffer** below."
    )

    if buffer_df.empty:
        st.success("🎉 Buffer is empty — every settled order matched a daily entry.")
    else:
        st.dataframe(buffer_df, use_container_width=True, hide_index=True)

        c1, c2 = st.columns([1, 1])
        if c1.button("♻️ Rematch Buffer", type="primary", use_container_width=True):
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
                    # No fee data here — preserve zero fees; full detail is from reconcile step.
                    net_payout = complete_amount
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO historical_archive
                            (order_no, goods_name, qty, selling_price, settlement_period,
                             complete_amount, commission, ds_processing_fee, fines, other_deductions, net_payout)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            b["order_no"],
                            a["goods_name"],
                            int(a["qty"] or 0),
                            float(a["selling_price"] or 0),
                            b["settlement_period"],
                            complete_amount,
                            0.0,
                            0.0,
                            0.0,
                            0.0,
                            net_payout,
                        ),
                    )
                    conn.execute("DELETE FROM active_daily_orders WHERE order_no = ?", (b["order_no"],))
                    conn.execute("DELETE FROM unkeyed_buffer WHERE order_no = ?", (b["order_no"],))
                    rematched += 1
            st.toast(f"Re-matched {rematched} buffer order(s).", icon="✅")
            st.rerun()

        if c2.button("🗑️ Clear Buffer", use_container_width=True):
            with get_conn() as conn:
                conn.execute("DELETE FROM unkeyed_buffer")
            st.toast("Buffer cleared.", icon="🧹")
            st.rerun()

# ---------------------------------------------------------------------------
# MODULE E — Lifetime Archive
# ---------------------------------------------------------------------------
with tab_archive:
    st.subheader("📚 Lifetime Historical Archive")
    if archive_df.empty:
        st.info("No settled orders archived yet.")
    else:
        with st.expander(f"View all {len(archive_df)} archived orders", expanded=True):
            st.dataframe(archive_df, use_container_width=True, hide_index=True)

        col1, col2 = st.columns(2)
        csv = archive_df.to_csv(index=False).encode("utf-8")
        col1.download_button(
            "⬇️ Download CSV",
            csv,
            file_name=f"kilimall_archive_{date.today().isoformat()}.csv",
            mime="text/csv",
            use_container_width=True,
        )
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            archive_df.to_excel(w, index=False, sheet_name="Archive")
        col2.download_button(
            "⬇️ Download Excel",
            buf.getvalue(),
            file_name=f"kilimall_archive_{date.today().isoformat()}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

st.divider()
st.caption(
    f"DB: `{DB_FILE}` · Active: {len(active_df)} · Buffer: {len(buffer_df)} · "
    f"Archived: {len(archive_df)} · Rendered {datetime.now():%Y-%m-%d %H:%M:%S}"
)
