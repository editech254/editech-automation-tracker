# pages/4_⚙️_Config.py
import re
import streamlit as st
import pandas as pd
from database import get_db_connection, create_user, log_audit

if not st.session_state.get("authenticated", False):
    st.warning("Please authenticate on the homepage first.")
    st.stop()

if st.session_state["role"] != "Admin":
    st.error("🛡️ Admin role required.")
    st.stop()

st.header("⚙️ System Control Panel")

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,50}$")

def _password_problems(pwd: str):
    problems = []
    if len(pwd) < 12: problems.append("≥12 chars")
    if not re.search(r"[A-Z]", pwd): problems.append("uppercase")
    if not re.search(r"[a-z]", pwd): problems.append("lowercase")
    if not re.search(r"\d", pwd): problems.append("digit")
    if not re.search(r"[^A-Za-z0-9]", pwd): problems.append("symbol")
    return problems

t1, t2 = st.tabs(["👥 Identity Access Management (RBAC)", "🏪 Subsidiary Mapping"])

with t1:
    st.subheader("Provision User Account")
    with st.form("create_user_form"):
        new_user = st.text_input("Username")
        new_pwd = st.text_input("Password", type="password")
        confirm = st.text_input("Confirm Password", type="password")
        assigned_role = st.selectbox("Role", ["Viewer", "Accountant", "Admin"])
        submit = st.form_submit_button("Create User")

        if submit:
            if not USERNAME_RE.match(new_user or ""):
                st.error("Invalid username (3–50 chars, letters/digits/_.-).")
            elif _password_problems(new_pwd or ""):
                st.error("Password must contain: " + ", ".join(_password_problems(new_pwd)))
            elif new_pwd != confirm:
                st.error("Passwords do not match.")
            else:
                try:
                    create_user(new_user, new_pwd, assigned_role)
                    log_audit(st.session_state["username"], f"Created user: {new_user} ({assigned_role})", "ADMIN")
                    st.success(f"User `{new_user}` created.")
                except Exception as ex:
                    st.error(f"Could not provision account: {ex}")

    st.divider()
    st.subheader("Existing Accounts")
    with get_db_connection() as conn:
        users_df = pd.read_sql("SELECT username, role, created_at FROM system_users ORDER BY created_at;", conn)
    st.dataframe(users_df, use_container_width=True, hide_index=True)

with t2:
    st.subheader("Shop Keyword Mapping")
    with get_db_connection() as conn:
        kws = pd.read_sql("SELECT keyword, shop_name FROM shop_keywords ORDER BY shop_name;", conn)
    st.dataframe(kws, use_container_width=True, hide_index=True)
