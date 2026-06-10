import streamlit as st
import pandas as pd

st.set_page_config(page_title="Order Reconciliation App", layout="wide")

st.title("📊 Order & Settlement Reconciliation System")

uploaded_file = st.file_uploader("Upload Excel File", type=["xlsx", "xls"])


# ----------------------------
# SAFE COLUMN HANDLER
# ----------------------------
def safe_get(df, col, default=""):
    """Return column if exists, else safe default"""
    if col in df.columns:
        return df[col]
    else:
        return pd.Series([default] * len(df))


# ----------------------------
# LOAD EXCEL FILE
# ----------------------------
def load_excel(file):
    xls = pd.ExcelFile(file)
    sheets = xls.sheet_names

    data = {}

    for sheet in sheets:
        df = pd.read_excel(xls, sheet_name=sheet)

        # Clean column names
        df.columns = [str(c).strip().lower() for c in df.columns]

        # FIX: Ensure order_no always exists
        if "order_no" not in df.columns:
            if len(df.columns) > 0:
                df["order_no"] = df.index.astype(str)
            else:
                df["order_no"] = "UNKNOWN"

        data[sheet.lower()] = df

    return data


# ----------------------------
# MAIN APP LOGIC
# ----------------------------
if uploaded_file:
    data = load_excel(uploaded_file)

    st.success("File loaded successfully!")

    # Show available sheets
    st.sidebar.header("📁 Sheets Found")
    sheet_names = list(data.keys())
    selected_sheet = st.sidebar.selectbox("Select Sheet", sheet_names)

    df = data[selected_sheet]

    st.subheader(f"📄 Viewing Sheet: {selected_sheet}")
    st.dataframe(df, use_container_width=True)

    # ----------------------------
    # IF ORDER SHEET EXISTS
    # ----------------------------
    order_sheets = [s for s in sheet_names if "order" in s]

    if order_sheets:
        st.subheader("🧾 Order Analysis")

        order_df = data[order_sheets[0]].copy()

        # Safe columns
        order_df["order_no"] = safe_get(order_df, "order_no", order_df.index.astype(str))
        order_df["amount"] = safe_get(order_df, "amount", 0)
        order_df["customer"] = safe_get(order_df, "customer", "Unknown")
        order_df["status"] = safe_get(order_df, "status", "unknown")

        st.write("### Cleaned Orders")
        st.dataframe(order_df, use_container_width=True)

        # ----------------------------
        # PAID / UNPAID LOGIC
        # ----------------------------
        paid_keywords = ["paid", "complete", "completed", "success"]

        order_df["is_paid"] = order_df["status"].astype(str).str.lower().apply(
            lambda x: any(k in x for k in paid_keywords)
        )

        paid_orders = order_df[order_df["is_paid"] == True]
        unpaid_orders = order_df[order_df["is_paid"] == False]

        col1, col2 = st.columns(2)

        with col1:
            st.metric("✅ Paid Orders", len(paid_orders))
            st.dataframe(paid_orders, use_container_width=True)

        with col2:
            st.metric("❌ Unpaid Orders", len(unpaid_orders))
            st.dataframe(unpaid_orders, use_container_width=True)

    # ----------------------------
    # SETTLEMENT SHEET LOGIC
    # ----------------------------
    settlement_sheets = [s for s in sheet_names if "settle" in s]

    if settlement_sheets:
        st.subheader("💰 Settlement Analysis")

        settle_df = data[settlement_sheets[0]].copy()

        settle_df["order_no"] = safe_get(settle_df, "order_no", settle_df.index.astype(str))
        settle_df["amount"] = safe_get(settle_df, "amount", 0)

        st.write("### Settlement Data")
        st.dataframe(settle_df, use_container_width=True)

        total_settled = pd.to_numeric(settle_df["amount"], errors="coerce").fillna(0).sum()

        st.metric("💰 Total Settled Amount", f"{total_settled:,.2f}")

    # ----------------------------
    # CROSS CHECK (ORDER vs SETTLEMENT)
    # ----------------------------
    if order_sheets and settlement_sheets:
        st.subheader("🔄 Reconciliation Report")

        order_df = data[order_sheets[0]].copy()
        settle_df = data[settlement_sheets[0]].copy()

        order_df["order_no"] = safe_get(order_df, "order_no", order_df.index.astype(str))
        settle_df["order_no"] = safe_get(settle_df, "order_no", settle_df.index.astype(str))

        merged = order_df.merge(
            settle_df[["order_no"]],
            on="order_no",
            how="left",
            indicator=True
        )

        merged["settled"] = merged["_merge"].apply(lambda x: x == "both")

        missing_settlements = merged[merged["settled"] == False]

        st.write("### ❌ Orders NOT in Settlement")
        st.dataframe(missing_settlements, use_container_width=True)

        st.metric("⚠️ Unsettled Orders", len(missing_settlements))

else:
    st.info("Upload an Excel file to begin analysis.")
