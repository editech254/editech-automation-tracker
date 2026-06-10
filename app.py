import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as px_go

st.set_page_config(page_title="E-commerce Reconciliation Dashboard", layout="wide")

st.title("📊 E-commerce Order & Settlement Reconciler")
st.markdown("Upload your **Order List** and multi-sheet **Settlement Report** to flag payment gaps and break down fees.")

# --- SIDEBAR UPLOADS ---
st.sidebar.header("📁 Data Upload Hub")
order_file = st.sidebar.file_uploader("1. Upload Order List (Excel/CSV)", type=["xlsx", "csv"])
settlement_file = st.sidebar.file_uploader("2. Upload Settlement Bill (Multi-sheet Excel)", type=["xlsx"])

# Helper function to normalize IDs safely
def clean_id(val):
    if pd.isna(val):
        return ""
    # Convert to string, remove decimal points if float-like, strip whitespaces and stray commas
    s = str(val).strip().split('.')[0]
    return s.replace('"', '').replace("'", "").replace(",", "").strip()

if order_file and settlement_file:
    try:
        # 1. Load Order List
        if order_file.name.endswith('.csv'):
            df_orders = pd.read_csv(order_file)
        else:
            df_orders = pd.read_excel(order_file)
            
        # 2. Load all sheets from settlement report
        xls = pd.ExcelFile(settlement_file)
        sheets = xls.sheet_names
        
        # Standardize order columns
        df_orders['Cleaned_Order_No'] = df_orders['Order Number'].apply(clean_id)
        
        # Dynamic Multi-sheet Reader & Parser
        settlement_orders = set()
        fee_summary = {
            "Total Commission": 0.0,
            "DS Processing Fee": 0.0,
            "Warehouse Operation Fee": 0.0,
            "Warehouse Storage fee": 0.0,
            "Fines": 0.0,
            "Other Deductions": 0.0,
            "Compensations": 0.0
        }
        
        # Parse sheets and map columns safely
        for sheet in sheets:
            df_sheet = pd.read_excel(xls, sheet_name=sheet)
            cols = [c.lower().strip() for c in df_sheet.columns]
            
            # Identify Order columns dynamically
            order_col_name = None
            for explicit_col in ['order_sn', 'order number', 'order_no', 'order no.']:
                if explicit_col in cols:
                    order_col_name = df_sheet.columns[cols.index(explicit_col)]
                    break
            
            if order_col_name:
                # Add found orders to the master paid registry
                valid_ids = df_sheet[order_col_name].dropna().apply(clean_id)
                settlement_orders.update(valid_ids.tolist())
            
            # Aggregate fees metrics safely based on known sheet contents or summary layout
            if 'bill' in sheet.lower() and not 'detail' in sheet.lower():
                # Extract macro values from summary row if present
                for k, col_idx in [("Total Commission", "total commission"), 
                                   ("DS Processing Fee", "ds processing fee"),
                                   ("Warehouse Operation Fee", "warehouse operation fee"),
                                   ("Warehouse Storage fee", "warehouse storage fee"),
                                   ("Fines", "fine"),
                                   ("Other Deductions", "other deductions"),
                                   ("Compensations", "compensations")]:
                    if col_idx in cols:
                        real_col = df_sheet.columns[cols.index(col_idx)]
                        fee_summary[k] += pd.to_numeric(df_sheet[real_col], errors='coerce').sum()
                        
            # Fallback fine aggregates directly from sub-sheets if summary values read zero
            elif 'fine' in sheet.lower():
                fine_col = [c for c in df_sheet.columns if 'fine' in c.lower()]
                if fine_col:
                    fee_summary["Fines"] += pd.to_numeric(df_sheet[fine_col[0]], errors='coerce').sum()
            elif 'processing' in sheet.lower():
                amount_col = [c for c in df_sheet.columns if 'amout' in c.lower() or 'amount' in c.lower()]
                if amount_col:
                    fee_summary["DS Processing Fee"] += pd.to_numeric(df_sheet[amount_col[0]], errors='coerce').sum()

        # --- RECONCILIATION ENGINE ---
        df_orders['Status'] = df_orders['Cleaned_Order_No'].apply(
            lambda x: 'Paid' if x in settlement_orders else 'Unpaid'
        )
        
        # Clean up presentation frame
        df_orders_display = df_orders.drop(columns=['Cleaned_Order_No'])

        # --- KPI DASHBOARD LAYOUT ---
        st.header("📈 Financial & Operational Health")
        
        total_orders_count = len(df_orders)
        paid_orders_df = df_orders[df_orders['Status'] == 'Paid']
        unpaid_orders_df = df_orders[df_orders['Status'] == 'Unpaid']
        
        kpi1, kpi2, kpi3, kpi4 = st.columns(4)
        kpi1.metric("Total Ordered SKUs", f"{total_orders_count}")
        kpi2.metric("Reconciled (Paid) Units", f"{len(paid_orders_df)}")
        kpi3.metric("Unreconciled (Unpaid) Units", f"{len(unpaid_orders_df)}", delta=f"-{len(unpaid_orders_df)} Pending")
        
        # Calculate dynamic Estimated Revenue
        if 'Deal Price' in df_orders.columns and 'Sold Qty' in df_orders.columns:
            estimated_revenue = (df_orders['Deal Price'] * df_orders['Sold Qty']).sum()
            kpi4.metric("Est. Total Pipeline Value", f"Ksh {estimated_revenue:,.2f}")
        else:
            kpi4.metric("Est. Total Pipeline Value", "N/A")

        # --- VISUALIZATIONS ---
        col_left, col_right = st.columns([1, 1])
        
        with col_left:
            st.subheader("Payout Completion Split")
            status_counts = df_orders['Status'].value_counts().reset_index()
            status_counts.columns = ['Payment Status', 'Count']
            fig_pie = px.pie(status_counts, values='Count', names='Payment Status', 
                             color='Payment Status', color_discrete_map={'Paid':'#2ecc71','Unpaid':'#e74c3c'},
                             hole=0.4)
            st.plotly_chart(fig_pie, use_container_width=True)
            
        with col_right:
            st.subheader("Settlement Deductions Breakdown")
            fee_df = pd.DataFrame(list(fee_summary.items()), columns=['Fee Component', 'Amount (Absolute Value)'])
            fee_df['Amount (Absolute Value)'] = fee_df['Amount (Absolute Value)'].abs() # Ensure positive values for clean bars
            fig_bar = px.bar(fee_df, x='Fee Component', y='Amount (Absolute Value)', 
                             color='Fee Component', text_auto='.2s',
                             labels={'Amount (Absolute Value)': 'Amount (Ksh)'})
            st.plotly_chart(fig_bar, use_container_width=True)

        # --- DATA FILTER AND BREAKDOWNS ---
        st.write("---")
        st.header("🔍 Order Audit Trail & Logs")
        
        status_filter = st.radio("Filter Order Inventory By Status:", ["All Orders", "Paid Orders Only", "Unpaid Log (Action Required)"], horizontal=True)
        
        if status_filter == "Paid Orders Only":
            filtered_df = df_orders_display[df_orders_display['Status'] == 'Paid']
        elif status_filter == "Unpaid Log (Action Required)":
            filtered_df = df_orders_display[df_orders_display['Status'] == 'Unpaid']
        else:
            filtered_df = df_orders_display

        st.dataframe(filtered_df, use_container_width=True)
        
        # Export Actions
        csv_buffer = filtered_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label=f"📥 Download {status_filter} Data View",
            data=csv_buffer,
            file_name=f"reconciled_{status_filter.lower().replace(' ', '_')}.csv",
            mime="text/csv"
        )
        
    except Exception as e:
        st.error(f"Error parsing system files: {str(e)}")
        st.info("Ensure that file labels, structures match traditional order sheet and billing breakdowns.")
else:
    # App landing message before documents upload
    st.info("💡 Please upload both your **Order SKU List** and your **Settlement Bill** file in the sidebar to start processing data.")
