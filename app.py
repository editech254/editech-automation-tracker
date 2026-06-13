# app.py — EDITECH ERP entrypoint
import re
import streamlit as st
from database import (
    init_db,
    get_db_connection,
    get_user,
    create_user,
    has_any_user,
    verify_password,
    log_audit,
)

st.set_page_config(
    page_title="EDITECH ERP — Order Lifecycle Suite",
    layout="wide",
    page_icon="⚖️",
)

# One-time schema init per session
if "db_initialized" not in st.session_state:
    init_db()
    st.session_state["db_initialized"] = True

# Session bootstrap
for k, v in {
    "authenticated": False,
    "username": None,
    "role": None,
    "global_shop": "All Shops",
}.items():
    st.session_state.setdefault(k, v)


# ---------------------------------------------------------------------------
# First-launch setup wizard
# ---------------------------------------------------------------------------
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,50}$")


def _password_problems(pwd: str) -> list[str]:
    problems = []
    if len(pwd) < 12:
        problems.append("at least 12 characters")
    if not re.search(r"[A-Z]", pwd):
        problems.append("an uppercase letter")
    if not re.search(r"[a-z]", pwd):
        problems.append("a lowercase letter")
    if not re.search(r"\d", pwd):
        problems.append("a digit")
    if not re.search(r"[^A-Za-z0-9]", pwd):
        problems.append("a symbol")
    return problems


def first_launch_setup():
    st.title("🚀 EDITECH ERP — First-Launch Setup")
    st.caption(
        "No accounts exist yet. Create the primary administrator. "
        "These credentials are not stored anywhere except the database — they live only in your head and your password manager."
    )
    with st.form("initial_admin_setup"):
        username = st.text_input("Administrator username", help="3–50 chars: letters, digits, '_', '.', '-'")
        pwd = st.text_input("Password", type="password")
        confirm = st.text_input("Confirm password", type="password")
        submit = st.form_submit_button("Create Administrator Account", type="primary")
        if submit:
            if not USERNAME_RE.match(username or ""):
                st.error("Invalid username. Use 3–50 chars: letters, digits, '_', '.', '-'.")
                return
            problems = _password_problems(pwd or "")
            if problems:
                st.error("Password must include " + ", ".join(problems) + ".")
                return
            if pwd != confirm:
                st.error("Passwords do not match.")
                return
            try:
                create_user(username, pwd, role="Admin")
                log_audit(username, "Initial admin account created", "BOOTSTRAP")
                st.success("Administrator created. Please sign in below.")
                st.rerun()
            except Exception as e:
                st.error(f"Could not create account: {e}")


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
def login_form():
    st.title("🔒 EDITECH Financial Systems — Sign In")
    st.subheader("Kilimall Order Lifecycle & Reconciliation Engine")
    with st.form("login_form"):
        user = st.text_input("Username")
        pwd = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign In", type="primary")
        if submitted:
            account = get_user(user)
            if account and verify_password(pwd, account["password_hash"]):
                st.session_state["authenticated"] = True
                st.session_state["username"] = user
                st.session_state["role"] = account["role"]
                log_audit(user, "User Login Successful", "AUTH")
                st.rerun()
            else:
                # Generic message — never reveal which field was wrong
                st.error("Invalid credentials.")
                log_audit(user or "(empty)", "Failed login attempt", "AUTH")


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
if not has_any_user():
    first_launch_setup()
    st.stop()

if not st.session_state["authenticated"]:
    login_form()
    st.stop()

# Authenticated shell
with st.sidebar:
    st.write(f"👤 **Operator:** {st.session_state['username']} (`{st.session_state['role']}`)")
    if st.button("Log Out"):
        log_audit(st.session_state["username"], "User Session Disconnect", "AUTH")
        for k in ("authenticated", "username", "role"):
            st.session_state[k] = None if k != "authenticated" else False
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
        index=0,
    )

st.title("⚖️ EDITECH Corporate Accounting Hub")
st.info("Select functional operations from the multi-page navigator pane in the sidebar.")
