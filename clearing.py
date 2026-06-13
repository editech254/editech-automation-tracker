# pages/3_🔄_Clearing.py
import streamlit as st
import pandas as pd
from database import get_db_connection, log_audit
from datetime import date

if not st.session_state.get("authenticated", False):
    st.warning("Please authenticate on the homepage first.")
    st.stop()

shop = st.session_state["global_shop"]
st.subheader("🔄 Automated Clearing & Settlement Matching Hub")

def clean_col(val):
    import re
    return re.sub(r"\D+", "", str(val).strip()) if pd.notna(val) else ""

# Execution Routine for Matching
def clear_settlement_file(file_obj, label):
    sheets = pd.read_excel(file_obj, sheet_name=None, dtype=str)
    bill_sheet_key = next((s for s in sheets.keys() if 'bill' in s.lower()), None)
    
    if not bill_sheet_key:
        raise ValueError("The uploaded statement file is missing the required 'Bill Details' tab.")
        
    bill_df = sheets[bill_sheet_key].copy()
    bill_df.columns = [c.strip().lower() for c in bill_df.columns.astype(str)]
    
    # Locate index columns safely
    o_col = next((c for c in bill_df.columns if 'order' in c or 'sn' in c), None)
    amt_col = next((c for c in bill_df.columns if 'amount' in c or 'complete' in c), None)
    comm_col = next((c for c in bill_df.columns if 'comm' in c), None)
    
    if not o_col or not amt_col:
        raise ValueError("Required mapping column keys were not found in the statement layout.")

    matched_count = 0
    suspense_count = 0

    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            for _, row in bill_df.iterrows():
                ord_no = clean_col(row[o_col])
                if not ord_no: continue
                
                gross_received = float(str(row[amt_col]).replace(",", "")) if pd.notna(row[amt_col]) else 0.0
                comm = float(str(row[comm_col]).replace(",", "")) if comm_col and pd.notna(row[comm_col]) else 0.0
                net_payout = gross_received - abs(comm)
                
                # Check for open unpaid invoice match
                c.execute("SELECT * FROM active_daily_orders WHERE order_no = %s", (ord_no,))
                invoice = c.fetchone()
                
                if invoice:
                    # Move to historical archive while preserving its original billing date
                    c.execute("""
                        INSERT INTO historical_archive (order_no, order_date, shop_name, goods_name, qty, selling_price, 
                                                       settlement_period, complete_amount, commission, ds_processing_fee, fines, other_deductions, net_payout)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 0, 0, 0, %s)
                        ON CONFLICT (order_no) DO NOTHING;
                    """, (ord_no, invoice['order_date'], invoice['shop_name'], invoice['goods_name'], invoice['qty'], invoice['selling_price'],
                          label, gross_received, comm, net_payout))
                    
                    # Remove from unpaid receivables ledger
                    c.execute("DELETE FROM active_daily_orders WHERE order_no = %s", (ord_no,))
                    matched_count += 1
                else:
                    # Routed to Suspense Buffer
                    c.execute("""
                        INSERT INTO unkeyed_buffer (order_no, shop_name, settlement_period, complete_amount, order_date)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (order_no) DO NOTHING;
                    """, (ord_no, shop if shop != "All Shops" else "EDITECH DIGITAL", label, gross_received, date.today()))
                    suspense_count += 1
                    
    return matched_count, suspense_count

# Layout Workflow
p_col, f_col = st.columns([2, 2])
period_lbl = p_col.text_input("Accounting Settlement Period Reference", value=f"Settlement Week-{date.today().strftime('%Y-%W')}")
stmt_file = f_col.file_uploader("Upload Kilimall Worksheet Data Document", type=["xlsx"])

if stmt_file and st.button("Execute Settlement Match & Balance Reconciliation Rollup", type="primary"):
    try:
        m, s = clear_settlement_file(stmt_file, period_lbl)
        log_audit(st.session_state["username"], f"Reconciled File: Matched {m}, Routed to Suspense {s}", "CLEARING")
        st.success(f"Clearing processing completed. Matched & Closed Invoices: {m}. Routed to Suspense Account: {s}.")
        st.rerun()
    except Exception as ex:
        st.error(f"Reconciliation processing halted: {ex}")

st.divider()
st.subheader("⚠️ Suspense Account Allocation Workspace (Unallocated Platform Collections)")

with get_db_connection() as conn:
    sql_buf = "SELECT order_no, shop_name, settlement_period, complete_amount, order_date FROM unkeyed_buffer"
    if shop != "All Shops": sql_buf += f" WHERE shop_name = '{shop}'"
    df_buf = pd.read_sql(sql_buf, conn)

if df_buf.empty:
    st.success("🎉 All cash collections match an open invoice line item.")
else:
    st.dataframe(df_buf, use_container_width=True, hide_index=True)
