# pages/4_⚙️_Config.py
import streamlit as st
import pandas as pd
from database import get_db_connection, hash_password, log_audit

if not st.session_state.get("authenticated", False):
    st.warning("Please authenticate on the homepage first.")
    st.stop()

if st.session_state["role"] != "Admin":
    st.error("🛡️ Security Access Exception: This administration workspace requires configuration level credentials.")
    st.stop()

st.header("⚙️ System Control Panel & Governance Dashboard")

t1, t2 = st.tabs(["👥 Identity Access Management (RBAC)", "🏪 Subsidiary Mapping Configurations"])

with t1:
    st.subheader("Create System Accounts")
    with st.form("Account Configuration Console"):
        new_user = st.text_input("Unique Username Label")
        new_pwd = st.text_input("Secret Cipher Password String", type="password")
        assigned_role = st.selectbox("Assigned Operational Privilege Level", ["Viewer", "Accountant", "Admin"])
        action_submit = st.form_submit_button("Provision User Credentials")
        
        if action_submit and new_user and new_pwd:
            try:
                with get_db_connection() as conn:
                    with conn.cursor() as c:
                        c.execute("INSERT INTO system_users VALUES (%s, %s, %s);", (new_user, hash_password(new_pwd), assigned_role))
                log_audit(st.session_state["username"], f"Created user entity account: {new_user}", "ADMIN")
                st.success(f"User account `{new_user}` provisioned successfully.")
            except Exception as ex:
                st.error(f"Could not provision account profile: {ex}")

with t2:
    st.subheader("Dynamic Platform Keyword Parsing Engine Rules")
    # Form layout parameters to safely wire shop identities inside postgres
    with get_db_connection() as conn:
        kws = pd.read_sql("SELECT keyword, shop_name FROM shop_keywords ORDER BY shop_name;", conn)
    st.dataframe(kws, use_container_width=True, hide_index=True)
