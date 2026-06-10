import streamlit as st
import pandas as pd

st.set_page_config(page_title="Reconciliation Dashboard", layout="wide")

st.title("📊 Orders vs Settlements Reconciliation System")


uploaded_file = st.file_uploader("Upload Excel File", type=["xlsx", "xls"])


# ---------------------------
# SAFE COLUMN HANDLER
# ---------------------------
def safe_col(df, col):
    if col in df.columns:
        return df[col]
    return pd.Series([None] * len(df))


# ---------------------------
# LOAD EXCEL
# ---------------------------
def load_excel(file):
    xls = pd.ExcelFile(file)
    data = {}

    for sheet in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=sheet)

        df.columns = [str(c).strip().lower() for c in df.columns]

        # ensure order_no always exists
        if "order_no" not in df.columns:
            df["order_no"] = df.index.astype(str)

        data[sheet.lower()] = df

    return data


# ---------------------------
# MAIN
# ---------------------------
if uploaded_file:

    data = load_excel(uploaded_file)

    st.success("File loaded successfully!")

    sheets = list(data.keys())

    order_sheet = [s for s in sheets if "order" in s]
    settlement_sheet = [s for s in sheets if "settle" in s]

    # ---------------------------
    # ORDERS
    # ---------------------------
    if order_sheet:
        orders = data[order_sheet[0]].copy()

        orders["order_no"] = safe_col(orders, "order_no")
        orders["amount"] = pd.to_numeric(safe_col(orders, "amount"), errors="coerce").fillna(0)
        orders["status"] = safe_col(orders, "status").fillna("unknown")

        st.subheader("🧾 Orders Data")
        st.dataframe(orders, use_container_width=True)

        # ---------------------------
        # PAYMENT CLASSIFICATION
        # ---------------------------
        paid_keywords = ["paid", "complete", "completed", "success", "done"]

        orders["is_paid"] = orders["status"].astype(str).str.lower().apply(
            lambda x: any(k in x for k in paid_keywords)
        )

        paid = orders[orders["is_paid"] == True]
        unpaid = orders[orders["is_paid"] == False]

        col1, col2, col3 = st.columns(3)

        total_orders = len(orders)
        total_revenue = orders["amount"].sum()
        paid_revenue = paid["amount"].sum()

        with col1:
            st.metric("📦 Total Orders", total_orders)

        with col2:
            st.metric("💰 Total Revenue", f"{total_revenue:,.2f}")

        with col3:
            coverage = (len(paid) / total_orders * 100) if total_orders else 0
            st.metric("📊 Payment Coverage %", f"{coverage:.2f}%")

        st.subheader("✅ Paid Orders")
        st.dataframe(paid, use_container_width=True)

        st.subheader("❌ Unpaid Orders")
        st.dataframe(unpaid, use_container_width=True)

    else:
        st.warning("No order sheet found (sheet name must contain 'order').")

    # ---------------------------
    # SETTLEMENTS
    # ---------------------------
    if settlement_sheet:
        settlements = data[settlement_sheet[0]].copy()

        settlements["order_no"] = safe_col(settlements, "order_no")
        settlements["amount"] = pd.to_numeric(safe_col(settlements, "amount"), errors="coerce").fillna(0)

        st.subheader("💰 Settlements Data")
        st.dataframe(settlements, use_container_width=True)

        total_settled = settlements["amount"].sum()

        st.metric("💰 Total Settled", f"{total_settled:,.2f}")

    else:
        st.warning("No settlement sheet found (sheet name must contain 'settle').")

    # ---------------------------
    # RECONCILIATION ENGINE
    # ---------------------------
    if order_sheet and settlement_sheet:

        st.subheader("🔄 Reconciliation Report")

        orders = data[order_sheet[0]].copy()
        settlements = data[settlement_sheet[0]].copy()

        orders["order_no"] = safe_col(orders, "order_no")
        settlements["order_no"] = safe_col(settlements, "order_no")

        # ---------------------------
        # MATCH ORDERS TO SETTLEMENTS
        # ---------------------------
        merged = orders.merge(
            settlements[["order_no"]],
            on="order_no",
            how="left",
            indicator=True
        )

        merged["settled"] = merged["_merge"] == "both"

        missing = merged[merged["settled"] == False]

        # ---------------------------
        # UNMATCHED SETTLEMENTS
        # ---------------------------
        unmatched_settlements = settlements.merge(
            orders[["order_no"]],
            on="order_no",
            how="left",
            indicator=True
        )

        orphan_settlements = unmatched_settlements[unmatched_settlements["_merge"] == "left_only"]

        # ---------------------------
        # DUPLICATE CHECK
        # ---------------------------
        duplicate_orders = orders[orders.duplicated("order_no", keep=False)]

        duplicate_settlements = settlements[settlements.duplicated("order_no", keep=False)]

        # ---------------------------
        # METRICS
        # ---------------------------
        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.metric("❌ Unpaid Orders", len(missing))

        with col2:
            st.metric("⚠️ Orphan Settlements", len(orphan_settlements))

        with col3:
            st.metric("🔁 Duplicate Orders", len(duplicate_orders))

        with col4:
            st.metric("🔁 Duplicate Settlements", len(duplicate_settlements))

        # ---------------------------
        # DETAILED TABLES
        # ---------------------------
        st.markdown("### ❌ Orders NOT SETTLED")
        st.dataframe(missing, use_container_width=True)

        st.markdown("### ⚠️ Orphan Settlements (No Matching Order)")
        st.dataframe(orphan_settlements, use_container_width=True)

        st.markdown("### 🔁 Duplicate Orders")
        st.dataframe(duplicate_orders, use_container_width=True)

        st.markdown("### 🔁 Duplicate Settlements")
        st.dataframe(duplicate_settlements, use_container_width=True)

        # ---------------------------
        # FINAL SUMMARY
        # ---------------------------
        st.subheader("📊 Final Summary")

        total_orders = len(orders)
        total_settled = len(settlements)
        matched = len(orders) - len(missing)

        st.write(f"""
        - Total Orders: **{total_orders}**
        - Total Settlements: **{total_settled}**
        - Matched Orders: **{matched}**
        - Unmatched Orders: **{len(missing)}**
        - Orphan Settlements: **{len(orphan_settlements)}**
        """)

else:
    st.info("Upload your Excel file to begin reconciliation.")
