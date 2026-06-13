# pages/1_📊_Dashboard.py
import streamlit as st
import pandas as pd
import plotly.express as px
from database import get_db_connection
import io

if not st.session_state.get("authenticated", False):
    st.warning("Please authenticate on the homepage first.")
    st.stop()

shop = st.session_state["global_shop"]

# Data Science Extraction Pipeline with Dataframes
with get_db_connection() as conn:
    query_unpaid = "SELECT * FROM active_daily_orders" if shop == "All Shops" else f"SELECT * FROM active_daily_orders WHERE shop_name = '{shop}'"
    query_paid = "SELECT * FROM historical_archive" if shop == "All Shops" else f"SELECT * FROM historical_archive WHERE shop_name = '{shop}'"
    
    df_unpaid = pd.read_sql(query_unpaid, conn)
    df_paid = pd.read_sql(query_paid, conn)

# Complete Metric Computations
val_unpaid = df_unpaid['selling_price'].sum() if not df_unpaid.empty else 0.0
val_paid = df_paid['net_payout'].sum() if not df_paid.empty else 0.0
gross_sales = (df_unpaid['selling_price'].sum() if not df_unpaid.empty else 0) + (df_paid['selling_price'].sum() if not df_paid.empty else 0)
total_deductions = (df_paid['commission'].abs().sum() + df_paid['ds_processing_fee'].abs().sum() + df_paid['fines'].abs().sum() + df_paid['other_deductions'].abs().sum()) if not df_paid.empty else 0.0

st.subheader(f"📊 Financial Dashboard & Health Scorecard — Scoped: {shop}")

m1, m2, m3, m4 = st.columns(4)
m1.metric("💰 Accrued Gross Billing", f"KES {gross_sales:,.2f}")
m2.metric("⏳ Accounts Receivable (Unpaid Invoices)", f"KES {val_unpaid:,.2f}", f"{len(df_unpaid)} Open Invoices")
m3.metric("🏦 Collected Cash Asset (Net Revenue Paid)", f"KES {val_paid:,.2f}")
m4.metric("✂️ Total Operational Platform Fees", f"KES {total_deductions:,.2f}")

st.divider()

tab_analytics, tab_logs = st.tabs(["📉 Revenue & Cost Analytics Charts", "📜 Compliance System Audit Logs"])

with tab_analytics:
    st.subheader("Data Visualization Dashboard")
    if not df_paid.empty:
        # Align timelines natively to Order Date parsed from user's files
        df_paid['order_date'] = pd.to_datetime(df_paid['order_date'])
        time_trend = df_paid.groupby('order_date')[['selling_price', 'net_payout']].sum().reset_index()
        fig = px.line(time_trend, x='order_date', y=['selling_price', 'net_payout'], 
                      labels={'value': 'Currency (KES)', 'order_date': 'Invoice Booking Timeline'},
                      title="Accrual Revenue vs Actual Net Cash Collections Trend Line")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Insufficient baseline data points available to render time trends.")

with tab_logs:
    st.subheader("Immutable Corporate Audit Trail")
    if st.session_state["role"] not in ["Admin", "Accountant"]:
        st.error("Security Privilege Deficit: Your role profile is restricted from reading system audit logs.")
    else:
        with get_db_connection() as conn:
            logs_df = pd.read_sql("SELECT * FROM system_audit_logs ORDER BY timestamp DESC LIMIT 200;", conn)
        st.dataframe(logs_df, use_container_width=True, hide_index=True)
