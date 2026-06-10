import streamlit as st
import pandas as pd
import sqlite3
import os
from datetime import datetime
import io
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
from reportlab.lib import colors

# ---------------- PAGE CONFIG ----------------
st.set_page_config(
    page_title="EDITECH Kilimall Dashboard",
    layout="wide",
    page_icon="📊",
)

# ---------------- DB SETUP ----------------
DB_DIR = os.environ.get("DB_DIR", "/data")
os.makedirs(DB_DIR, exist_ok=True)
DB_FILE = os.path.join(DB_DIR, "editech_kilimall.db")


def get_conn():
    return sqlite3.connect(DB_FILE, check_same_thread=False)


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS master_orders (
        order_no TEXT PRIMARY KEY,
        shop_id TEXT,
        shop_name TEXT,
        product_name TEXT,
        quantity INTEGER,
        amount REAL,
        cost REAL DEFAULT 0,
        commission REAL DEFAULT 0,
        profit REAL DEFAULT 0,
        order_time TEXT,
        status TEXT,
        logged_via TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS settlement_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_no TEXT,
        amount REAL,
        settlement_date TEXT,
        source_sheet TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    conn.close()


init_db()

# ---------------- CONFIG ----------------
COMMISSION_RATE = 0.12
WHATSAPP_NUMBER = "254713522120"

# ---------------- HELPERS ----------------
def read_file(file):
    if file.name.endswith(".csv"):
        return pd.read_csv(file)
    return pd.read_excel(file)


def normalize(df):
    df.columns = [c.lower().strip().replace(" ", "_") for c in df.columns]
    return df


def clean_order(series):
    return series.astype(str).str.replace(r"[^\d]", "", regex=True)


# ---------------- ORDER INGEST ----------------
def save_orders(df, source):
    conn = get_conn()
    c = conn.cursor()

    inserted, skipped = 0, 0

    for _, row in df.iterrows():
        order_no = row.get("order_no")
        if not order_no:
            skipped += 1
            continue

        amount = float(row.get("amount") or 0)
        cost = float(row.get("cost") or 0)
        commission = amount * COMMISSION_RATE
        profit = amount - cost - commission

        try:
            c.execute("""
            INSERT INTO master_orders
            (order_no, shop_id, shop_name, product_name, quantity,
             amount, cost, commission, profit, order_time, status, logged_via)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(order_no),
                str(row.get("shop_id") or ""),
                str(row.get("shop_name") or ""),
                str(row.get("product_name") or ""),
                int(row.get("quantity") or 0),
                amount,
                cost,
                commission,
                profit,
                str(row.get("order_time") or ""),
                str(row.get("status") or ""),
                source
            ))
            inserted += 1
        except sqlite3.IntegrityError:
            skipped += 1

    conn.commit()
    conn.close()
    return inserted, skipped


# ---------------- SETTLEMENT INGEST ----------------
def read_settlement(file):
    xls = pd.ExcelFile(file)
    frames = []

    for sheet in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=sheet)
        df = normalize(df)

        order_col = next((c for c in df.columns if "order" in c), None)
        amount_col = next((c for c in df.columns if "amount" in c or "paid" in c), None)
        date_col = next((c for c in df.columns if "date" in c), None)

        temp = pd.DataFrame()
        temp["order_no"] = df[order_col] if order_col else None
        temp["amount"] = df[amount_col] if amount_col else 0
        temp["settlement_date"] = df[date_col] if date_col else ""
        temp["source_sheet"] = sheet

        frames.append(temp)

    return pd.concat(frames, ignore_index=True)


def save_settlement(df):
    conn = get_conn()
    c = conn.cursor()

    for _, row in df.iterrows():
        if not row["order_no"]:
            continue

        c.execute("""
        INSERT INTO settlement_records (order_no, amount, settlement_date, source_sheet)
        VALUES (?, ?, ?, ?)
        """, (
            str(row["order_no"]),
            float(row["amount"] or 0),
            str(row["settlement_date"] or ""),
            str(row["source_sheet"])
        ))

    conn.commit()
    conn.close()


# ---------------- DATA FETCH ----------------
def fetch_orders():
    conn = get_conn()
    df = pd.read_sql("SELECT * FROM master_orders", conn)
    conn.close()
    return df


def fetch_settlements():
    conn = get_conn()
    df = pd.read_sql("SELECT * FROM settlement_records", conn)
    conn.close()
    return df


# ---------------- RECONCILIATION ----------------
def reconcile():
    orders = fetch_orders()
    settlements = fetch_settlements()

    if orders.empty:
        return pd.DataFrame()

    orders["order_no"] = orders["order_no"].astype(str)

    if not settlements.empty:
        settlements["order_no"] = settlements["order_no"].astype(str)

        merged = orders.merge(
            settlements,
            on="order_no",
            how="left",
            suffixes=("_order", "_settlement")
        )

        merged["reconciliation_status"] = merged["amount_settlement"].apply(
            lambda x: "PAID" if pd.notna(x) and x > 0 else "MISSING"
        )
    else:
        merged = orders.copy()
        merged["reconciliation_status"] = "MISSING"

    return merged


# ---------------- REPORTS ----------------
def monthly_report(df):
    df["order_time"] = pd.to_datetime(df["order_time"], errors="coerce")

    return df.groupby(df["order_time"].dt.to_period("M")).agg({
        "amount": "sum",
        "commission": "sum",
        "profit": "sum",
        "order_no": "count"
    }).reset_index().rename(columns={"order_no": "orders"})


# ---------------- PDF ----------------
def generate_pdf(data):
    file = "report.pdf"
    doc = SimpleDocTemplate(file)
    table = Table(data)

    style = TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.grey),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("GRID", (0,0), (-1,-1), 0.5, colors.black),
    ])

    table.setStyle(style)
    doc.build([table])
    return file


# ---------------- WHATSAPP ----------------
def whatsapp(order_no, amount):
    msg = f"Unpaid Order Alert: {order_no} Amount: {amount}"
    return f"https://wa.me/{WHATSAPP_NUMBER}?text={msg}"


# ---------------- UI ----------------
st.title("📊 EDITECH Kilimall Accounting Dashboard")

tab1, tab2, tab3, tab4 = st.tabs([
    "📤 Upload Orders",
    "💰 Settlement",
    "🔗 Reconciliation",
    "📈 Analytics"
])

# ---------------- ORDERS ----------------
with tab1:
    file = st.file_uploader("Upload Orders", type=["csv", "xlsx"])

    if file and st.button("Process Orders"):
        df = normalize(read_file(file))
        df["order_no"] = clean_order(df["order_no"])

        ins, skip = save_orders(df, "upload")
        st.success(f"Inserted: {ins}, Skipped: {skip}")

# ---------------- SETTLEMENT ----------------
with tab2:
    file2 = st.file_uploader("Upload Settlement (Multi-sheet Excel)", type=["xlsx"])

    if file2 and st.button("Process Settlement"):
        df = read_settlement(file2)
        save_settlement(df)
        st.success(f"Loaded {len(df)} settlement records")

# ---------------- RECONCILIATION ----------------
with tab3:
    df = reconcile()

    if df.empty:
        st.info("No data yet")
    else:
        st.dataframe(df, use_container_width=True)

        st.subheader("❌ Unpaid Orders")
        unpaid = df[df["reconciliation_status"] == "MISSING"]

        for _, row in unpaid.iterrows():
            st.markdown(f"[Send WhatsApp Alert]({whatsapp(row['order_no'], row['amount'])})")

        if st.button("Generate PDF"):
            pdf = generate_pdf(df.head(50).values.tolist())
            with open(pdf, "rb") as f:
                st.download_button("Download PDF", f, file_name="report.pdf")

# ---------------- ANALYTICS ----------------
with tab4:
    df = fetch_orders()

    if df.empty:
        st.info("No data")
    else:
        st.metric("Orders", len(df))
        st.metric("Revenue", f"{df['amount'].sum():,.2f}")
        st.metric("Profit", f"{df['profit'].sum():,.2f}")

        st.subheader("Monthly Report")
        st.dataframe(monthly_report(df))
