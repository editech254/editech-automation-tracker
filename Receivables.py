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
st.subheader("📝 Open Accounts Receivable Ledger")

_DIGIT_RE = re.compile(r"\D+")
def clean_order_no(value) -> str:
    if value is None or str(value).lower() == "nan": return ""
    return _DIGIT_RE.sub("", str(value).strip())


with st.expander("📥 Bulk Upload Dispatched Invoices (Excel / CSV)"):
    uploaded_file = st.file_uploader("Select statement document", type=["xlsx", "xls", "csv"])
    if uploaded_file and st.button("Parse & Append Rows"):
        try:
            df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith(".csv") else pd.read_excel(uploaded_file)
            df.columns = [c.strip().lower() for c in df.columns.astype(str)]

            order_col = next((c for c in df.columns if c in ["order_no", "order_sn", "order number", "order_id"]), None)
            date_col = next((c for c in df.columns if c in ["date", "order_date", "created_at", "dispatch_date"]), None)
            price_col = next((c for c in df.columns if c in ["selling_price", "price", "amount"]), None)
            goods_col = next((c for c in df.columns if c in ["goods_name", "product", "item"]), None)
            qty_col = next((c for c in df.columns if c in ["qty", "quantity"]), None)

            if not order_col or not date_col:
                st.error("Missing required columns: order_no and order_date.")
            else:
                inserted = 0
                with get_db_connection() as conn:
                    with conn.cursor() as c:
                        for _, row in df.iterrows():
                            o_no = clean_order_no(row[order_col])
                            if not o_no: continue
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
                                    order_date = EXCLUDED.order_date,
                                    selling_price = EXCLUDED.selling_price;
                            """, (o_date, o_no, s_name, goods, qty, price))
                            inserted += 1
                log_audit(st.session_state["username"], f"Bulk invoiced {inserted} records", "RECEIVABLES")
                st.success(f"Processed {inserted} items into open ledger.")
                st.rerun()
        except Exception as e:
            st.error(f"Execution error: {e}")


# Ledger
with get_db_connection() as conn:
    if shop == "All Shops":
        ledger_df = pd.read_sql(
            "SELECT order_date, order_no, shop_name, goods_name, qty, selling_price "
            "FROM active_daily_orders ORDER BY order_date DESC", conn)
    else:
        ledger_df = pd.read_sql(
            "SELECT order_date, order_no, shop_name, goods_name, qty, selling_price "
            "FROM active_daily_orders WHERE shop_name = %(s)s ORDER BY order_date DESC",
            conn, params={"s": shop})

st.write("Edit invoice rows directly below:")
select_all = st.checkbox("Select all rows for removal", value=False)
ledger_df.insert(0, "🗑️ Purge", select_all)

edited_data = st.data_editor(
    ledger_df,
    num_rows="dynamic",
    use_container_width=True,
    key="receivables_editor",
    column_config={
        "order_date": st.column_config.DateColumn("Invoice Date", required=True),
        "order_no": st.column_config.TextColumn("Order No", required=True),
        "qty": st.column_config.NumberColumn("Quantity"),
        "selling_price": st.column_config.NumberColumn("Gross Value (KES)", format="%.2f"),
    },
)

c1, c2, _ = st.columns([2, 2, 4])
if c1.button("💾 Save Changes", type="primary"):
    if st.session_state["role"] == "Viewer":
        st.error("Viewers cannot save modifications.")
    else:
        with get_db_connection() as conn:
            with conn.cursor() as c:
                if shop != "All Shops":
                    c.execute("DELETE FROM active_daily_orders WHERE shop_name = %s", (shop,))
                else:
                    c.execute("DELETE FROM active_daily_orders")
                keep_rows = edited_data[~edited_data["🗑️ Purge"].fillna(False)]
                for _, r in keep_rows.iterrows():
                    c.execute("""
                        INSERT INTO active_daily_orders (order_date, order_no, shop_name, goods_name, qty, selling_price)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (r["order_date"], r["order_no"], r["shop_name"], r["goods_name"], int(r["qty"]), float(r["selling_price"])))
        log_audit(st.session_state["username"], f"Modified receivables for scope: {shop}", "RECEIVABLES")
        st.success("Ledger updated.")
        st.rerun()

csv_data = ledger_df.to_csv(index=False).encode("utf-8")
c2.download_button("📥 Export Open Receivables (CSV)", csv_data, "unpaid_invoices.csv", "text/csv")
