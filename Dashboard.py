# pages/1_📊_Dashboard.py
import streamlit as st
import pandas as pd
import plotly.express as px
from database import get_db_connection

if not st.session_state.get("authenticated", False):
    st.warning("Please authenticate on the homepage first.")
    st.stop()

shop = st.session_state["global_shop"]

with get_db_connection() as conn:
    if shop == "All Shops":
        df_unpaid = pd.read_sql("SELECT * FROM active_daily_orders", conn)
        df_paid = pd.read_sql("SELECT * FROM historical_archive", conn)
    else:
        df_unpaid = pd.read_sql(
            "SELECT * FROM active_daily_orders WHERE shop_name = %(s)s", conn, params={"s": shop})
        df_paid = pd.read_sql(
            "SELECT * FROM historical_archive WHERE shop_name = %(s)s", conn, params={"s": shop})

val_unpaid = df_unpaid["selling_price"].sum() if not df_unpaid.empty else 0.0
val_paid = df_paid["net_payout"].sum() if not df_paid.empty else 0.0
gross_sales = (df_unpaid["selling_price"].sum() if not df_unpaid.empty else 0) + \
              (df_paid["selling_price"].sum() if not df_paid.empty else 0)
total_deductions = 0.0
if not df_paid.empty:
    for col in ("commission", "ds_processing_fee", "fines", "other_deductions"):
        total_deductions += df_paid[col].abs().sum()

st.subheader(f"📊 Financial Dashboard — Scoped: {shop}")

m1, m2, m3, m4 = st.columns(4)
m1.metric("💰 Accrued Gross Billing", f"KES {gross_sales:,.2f}")
m2.metric("⏳ Accounts Receivable", f"KES {val_unpaid:,.2f}", f"{len(df_unpaid)} open")
m3.metric("🏦 Net Cash Collected", f"KES {val_paid:,.2f}")
m4.metric("✂️ Platform Fees", f"KES {total_deductions:,.2f}")

st.divider()

tab_analytics, tab_logs = st.tabs(["📉 Revenue Analytics", "📜 Audit Logs"])

with tab_analytics:
    if not df_paid.empty:
        df_paid["order_date"] = pd.to_datetime(df_paid["order_date"])
        trend = df_paid.groupby("order_date")[["selling_price", "net_payout"]].sum().reset_index()
        fig = px.line(
            trend, x="order_date", y=["selling_price", "net_payout"],
            labels={"value": "KES", "order_date": "Invoice Booking Timeline"},
            title="Accrual Revenue vs Net Cash Collections",
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Insufficient data to render trends.")

with tab_logs:
    if st.session_state["role"] not in ("Admin", "Accountant"):
        st.error("Role restricted from viewing audit logs.")
    else:
        with get_db_connection() as conn:
            logs_df = pd.read_sql(
                "SELECT * FROM system_audit_logs ORDER BY timestamp DESC LIMIT 200;", conn)
        st.dataframe(logs_df, use_container_width=True, hide_index=True)
