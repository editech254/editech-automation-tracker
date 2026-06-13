# pages/2_📝_Receivables.py
import streamlit as st
import pandas as pd
import re
from datetime import date
from database import get_db_connection, log_audit

if not st.session_state.get("authenticated", False):
    st.warning("Please authenticate on the homepage first.")
    st.stop()

shop = st.session_state["global_shop"]
st.subheader("📝 Open Accounts Receivable Ledger (Unpaid Open Invoices)")

# Standard Cleanup Methods matching original specifications
_DIGIT_RE = re.compile(r"\D+")
def clean_order_no(value) -> str:
    if value is None or str(value).lower() == "nan": return ""
    return _DIGIT_RE.sub("", str(value).strip())

# File Load & Parsing Wizard Frame
with st.expander("📥 Bulk Upload Dispatched Invoices (Excel / CSV Pipeline)"):
    uploaded_file = st.file_uploader("Select Statement Document", type=["xlsx", "xls", "csv"])
    if uploaded_file and st.button("Execute Stream Parser & Append Rows"):
        try:
            df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
            
            # Extract historical date mapping dynamically to protect data timeline fidelity
            df.columns = [c.strip().lower() for c in df.columns.astype(str)]
            
            # Auto Mapping vectors matching historical properties
            order_col = next((c for c in df.columns if c in ['order_no', 'order_sn', 'order number', 'order_id']), None)
            date_col = next((c for c in df.columns if c in ['date', 'order_date', 'created_at', 'dispatch_date']), None)
            price_col = next((c for c in df.columns if c in ['selling_price', 'price', 'amount']), None)
            goods_col = next((c for c in df.columns if c in ['goods_name', 'product', 'item']), None)
            qty_col = next((c for c in df.columns if c in ['qty', 'quantity']), None)

            if not order_col or not date_col:
                st.error("Missing critical identifiers: Order No and Order Date are required.")
            else:
                with get_db_connection() as conn:
                    with conn.cursor() as c:
                        inserted = 0
                        for _, row in df.iterrows():
                            o_no = clean_order_no(row[order_col])
                            if not o_no: continue
                            
                            # Parse dates into explicit native dates
                            raw_date = row[date_col]
                            o_date = pd.to_datetime(raw_date).date() if pd.notna(raw_date) else date.today()
                            
                            price = float(str(row[price_col]).replace(",", "")) if price_col in df.columns and pd.notna(row[price_col]) else 0.0
                            goods = str(row[goods_col]) if goods_col in df.columns and pd.notna(row[goods_col]) else "Generic SKU"
                            qty = int(row[qty_col]) if qty_col in df.columns and pd.notna(row[qty_col]) else 1
                            s_name = shop if shop != "All Shops" else "EDITECH DIGITAL"

                            c.execute("""
                                INSERT INTO active_daily_orders (order_date, order_no, shop_name, goods_name, qty, selling_price)
                                VALUES (%s, %s, %s, %s, %s, %s)
                                ON CONFLICT (order_no) DO UPDATE SET 
                                    order_date = EXCLUDED.order_date, selling_price = EXCLUDED.selling_price;
                            """, (o_date, o_no, s_name, goods, qty, price))
                            inserted += 1
                
                log_audit(st.session_state["username"], f"Bulk Invoiced {inserted} Records via file", "RECEIVABLES")
                st.success(f"Successfully processed {inserted} items into open ledger profiles.")
                st.rerun()
        except Exception as e:
            st.error(f"Execution Error: {e}")

# Ledger Management View Frame via st.data_editor
with get_db_connection() as conn:
    sql = "SELECT order_date, order_no, shop_name, goods_name, qty, selling_price FROM active_daily_orders"
    if shop != "All Shops":
        sql += f" WHERE shop_name = '{shop}'"
    sql += " ORDER BY order_date DESC"
    ledger_df = pd.read_sql(sql, conn)

st.write("Edit invoice rows directly below:")
select_all = st.checkbox("Select All Active Rows For Removal Actions", value=False)
ledger_df.insert(0, "🗑️ Purge", select_all)

edited_data = st.data_editor(
    ledger_df,
    num_rows="dynamic",
    use_container_width=True,
    key="receivables_editor",
    column_config={
        "order_date": st.column_config.DateColumn("Invoice Book Date", required=True),
        "order_no": st.column_config.TextColumn("Invoice Order No", required=True),
        "qty": st.column_config.NumberColumn("Quantity"),
        "selling_price": st.column_config.NumberColumn("Selling Gross Value (KES)", format="%.2f")
    }
)

c1, c2, _ = st.columns([2, 2, 4])
if c1.button("💾 Save Changes", type="primary"):
    if st.session_state["role"] == "Viewer":
        st.error("Action Prohibited: Viewers cannot save modifications.")
    else:
        # Save mutations to Postgres backend
        with get_db_connection() as conn:
            with conn.cursor() as c:
                # To maintain isolation, clean current scoped metrics and rewrite data mappings safely
                if shop != "All Shops":
                    c.execute("DELETE FROM active_daily_orders WHERE shop_name = %s", (shop,))
                else:
                    c.execute("DELETE FROM active_daily_orders")
                
                keep_rows = edited_data[~edited_data["🗑️ Purge"].fillna(False)]
                for _, r in keep_rows.iterrows():
                    c.execute("""
                        INSERT INTO active_daily_orders (order_date, order_no, shop_name, goods_name, qty, selling_price)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (r['order_date'], r['order_no'], r['shop_name'], r['goods_name'], int(r['qty']), float(r['selling_price'])))
        log_audit(st.session_state["username"], f"Modified open receivables table for store view {shop}", "RECEIVABLES")
        st.success("Ledger states updated successfully.")
        st.rerun()

# Download Pipeline Interface Engine
csv_data = ledger_df.to_csv(index=False).encode('utf-8')
c2.download_button("📥 Export Open Receivables Report (CSV)", csv_data, "unpaid_invoices.csv", "text/csv")
