# pages/3_🔄_Clearing.py
import re
import streamlit as st
import pandas as pd
import psycopg2.extras
from database import get_db_connection, log_audit
from datetime import date

if not st.session_state.get("authenticated", False):
    st.warning("Please authenticate on the homepage first.")
    st.stop()

shop = st.session_state["global_shop"]
st.subheader("🔄 Automated Clearing & Settlement Matching Hub")

_DIGIT_RE = re.compile(r"\D+")
def clean_col(val):
    return _DIGIT_RE.sub("", str(val).strip()) if pd.notna(val) else ""

def _num(v):
    if v is None or (isinstance(v, float) and pd.isna(v)): return 0.0
    try:
        return float(str(v).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def clear_settlement_file(file_obj, label):
    """Match a multi-sheet Kilimall settlement workbook against the receivables ledger."""
    sheets = pd.read_excel(file_obj, sheet_name=None, dtype=str)

    def find_sheet(token):
        return next((s for s in sheets if token in s.lower()), None)

    bill_key = find_sheet("bill")
    if not bill_key:
        raise ValueError("Statement file missing required 'Bill Details' tab.")
    bill_df = sheets[bill_key].copy()
    bill_df.columns = [c.strip().lower() for c in bill_df.columns.astype(str)]
    o_col = next((c for c in bill_df.columns if "order" in c or "sn" in c), None)
    amt_col = next((c for c in bill_df.columns if "amount" in c or "complete" in c), None)
    comm_col = next((c for c in bill_df.columns if "comm" in c), None)
    if not o_col or not amt_col:
        raise ValueError("Required columns not found in Bill Details.")

    # Optional sheets (warn but do not fail)
    def collect(token, order_token, amt_token):
        key = find_sheet(token)
        if not key:
            st.toast(f"Optional sheet '{token}' not found — skipping.", icon="⚠️")
            return {}
        df = sheets[key].copy()
        df.columns = [c.strip().lower() for c in df.columns.astype(str)]
        oc = next((c for c in df.columns if order_token in c), None)
        ac = next((c for c in df.columns if amt_token in c), None)
        if not oc or not ac: return {}
        out = {}
        for _, r in df.iterrows():
            on = clean_col(r[oc])
            if on:
                out[on] = out.get(on, 0.0) + _num(r[ac])
        return out

    ds_fees = collect("ds processing", "order", "amou")
    fines = collect("fine", "order", "fine")
    other = collect("other", "order", "amount")

    matched_count = 0
    suspense_count = 0

    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            for _, row in bill_df.iterrows():
                ord_no = clean_col(row[o_col])
                if not ord_no: continue
                gross = _num(row[amt_col])
                comm = _num(row[comm_col]) if comm_col else 0.0
                ds = ds_fees.get(ord_no, 0.0)
                fn = fines.get(ord_no, 0.0)
                ot = other.get(ord_no, 0.0)
                net = gross - abs(comm) - abs(ds) - abs(fn) - abs(ot)

                c.execute("SELECT * FROM active_daily_orders WHERE order_no = %s", (ord_no,))
                invoice = c.fetchone()

                if invoice:
                    c.execute("""
                        INSERT INTO historical_archive (order_no, order_date, shop_name, goods_name, qty, selling_price,
                                                       settlement_period, complete_amount, commission, ds_processing_fee, fines, other_deductions, net_payout)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (order_no) DO NOTHING;
                    """, (ord_no, invoice["order_date"], invoice["shop_name"], invoice["goods_name"],
                          invoice["qty"], invoice["selling_price"], label, gross, comm, ds, fn, ot, net))
                    c.execute("DELETE FROM active_daily_orders WHERE order_no = %s", (ord_no,))
                    matched_count += 1
                else:
                    c.execute("""
                        INSERT INTO unkeyed_buffer (order_no, shop_name, settlement_period, complete_amount, order_date)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (order_no) DO UPDATE SET complete_amount = EXCLUDED.complete_amount;
                    """, (ord_no, shop if shop != "All Shops" else "EDITECH DIGITAL", label, gross, date.today()))
                    suspense_count += 1

    return matched_count, suspense_count


def rematch_buffer():
    """Re-scan the suspense buffer against the live receivables ledger.
    For each suspense row whose order_no now exists in active_daily_orders,
    move it into historical_archive and clear both source rows.
    """
    moved = 0
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            c.execute("SELECT * FROM unkeyed_buffer;")
            for buf in c.fetchall():
                c.execute("SELECT * FROM active_daily_orders WHERE order_no = %s", (buf["order_no"],))
                inv = c.fetchone()
                if not inv: continue
                gross = float(buf["complete_amount"] or 0)
                c.execute("""
                    INSERT INTO historical_archive (order_no, order_date, shop_name, goods_name, qty, selling_price,
                                                   settlement_period, complete_amount, commission, ds_processing_fee, fines, other_deductions, net_payout)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, 0, 0, 0, %s)
                    ON CONFLICT (order_no) DO NOTHING;
                """, (buf["order_no"], inv["order_date"], inv["shop_name"], inv["goods_name"], inv["qty"],
                      inv["selling_price"], buf["settlement_period"], gross, gross))
                c.execute("DELETE FROM active_daily_orders WHERE order_no = %s", (buf["order_no"],))
                c.execute("DELETE FROM unkeyed_buffer WHERE order_no = %s", (buf["order_no"],))
                moved += 1
    return moved


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
p_col, f_col = st.columns([2, 2])
period_lbl = p_col.text_input("Settlement Period Reference", value=f"Settlement Week-{date.today().strftime('%Y-%W')}")
stmt_file = f_col.file_uploader("Upload Kilimall Settlement Workbook", type=["xlsx"])

if stmt_file and st.button("Execute Settlement Match", type="primary"):
    try:
        m, s = clear_settlement_file(stmt_file, period_lbl)
        log_audit(st.session_state["username"], f"Reconciled: matched {m}, suspense {s}", "CLEARING")
        st.success(f"Matched & archived: {m}. Routed to suspense: {s}.")
        st.rerun()
    except Exception as ex:
        st.error(f"Reconciliation halted: {ex}")

st.divider()
st.subheader("⚠️ Suspense Buffer (Unallocated Platform Collections)")

with get_db_connection() as conn:
    if shop == "All Shops":
        df_buf = pd.read_sql("SELECT order_no, shop_name, settlement_period, complete_amount, order_date FROM unkeyed_buffer", conn)
    else:
        df_buf = pd.read_sql(
            "SELECT order_no, shop_name, settlement_period, complete_amount, order_date FROM unkeyed_buffer WHERE shop_name = %(s)s",
            conn, params={"s": shop})

if df_buf.empty:
    st.success("🎉 All cash collections match an open invoice line item.")
else:
    st.dataframe(df_buf, use_container_width=True, hide_index=True)
    if st.button("🔁 Rematch Buffer Against Receivables"):
        moved = rematch_buffer()
        log_audit(st.session_state["username"], f"Rematch buffer cleared {moved} rows", "CLEARING")
        st.success(f"Rematched and archived {moved} buffer rows.")
        st.rerun()
