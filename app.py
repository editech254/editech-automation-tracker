# app.py
import streamlit as st
import pandas as pd
from database import init_db, get_db_connection, hash_password, log_audit

st.set_page_config(
    page_title="EDITECH ERP — Order Lifecycle Suite",
    layout="wide",
    page_icon="⚖️"
)

# Initialize Database Schema Instantiations
if 'db_initialized' not in st.session_state:
    init_db()
    st.session_state['db_initialized'] = True

# Session State Initialization for Security & Multi-tenancy
if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False
    st.session_state["username"] = None
    st.session_state["role"] = None
if "global_shop" not in st.session_state:
    st.session_state["global_shop"] = "All Shops"

def login_form():
    st.title("🔒 EDITECH Financial Systems Control Gate")
    st.subheader("Kilimall Order Lifecycle & Accrual Reconciliation Engine")
    
    with st.form("Login System Wrapper"):
        user = st.text_input("Username Identifier")
        pwd = st.text_input("Security Access Code (Password)", type="password")
        submitted = st.form_submit_button("Authenticate Sign-In Token")
        
        if submitted:
            with get_db_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as c:
                    c.execute("SELECT * FROM system_users WHERE username = %s", (user,))
                    account = c.fetchone()
                    if account and account['password_hash'] == hash_password(pwd):
                        st.session_state["authenticated"] = True
                        st.session_state["username"] = user
                        st.session_state["role"] = account['role']
                        log_audit(user, "User Login Successful", "AUTH")
                        st.success(f"Access granted: Session token generated for User Role: {account['role']}.")
                        st.user_info  # Optional performance check
                        st.rerun()
                    else:
                        st.error("Access Refused: Invalid token credentials matched.")

if not st.session_state["authenticated"]:
    login_form()
    st.stop()
else:
    # Sidebar Multi-tenant Controller Hook
    with st.sidebar:
        st.write(f"👤 **Operator:** {st.session_state['username']} (`{st.session_state['role']}`)")
        if st.button("Log Out of System"):
            log_audit(st.session_state["username"], "User Session Disconnect", "AUTH")
            st.session_state["authenticated"] = False
            st.rerun()
            
        st.divider()
        st.header("🏪 Global Subsidiary Filter")
        
        with get_db_connection() as conn:
            with conn.cursor() as c:
                c.execute("SELECT shop_name FROM registered_shops ORDER BY shop_name;")
                shops = [r[0] for r in c.fetchall()]
        
        st.session_state["global_shop"] = st.selectbox(
            "Scoped Entity Viewport",
            options=["All Shops"] + shops,
            index=0
        )
    
    st.title("⚖️ EDITECH Corporate Accounting Hub")
    st.info("Select functional operations from the multi-page navigator pane in the sidebar.")
