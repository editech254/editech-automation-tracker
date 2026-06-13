# database.py
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
import streamlit as st
import hashlib

# Fetch Postgres URL from environment variables
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/editech_db")

@contextmanager
def get_db_connection():
    """Context manager for safe PostgreSQL transactional execution."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def init_db():
    """Initializes schema components natively matching accrual accounting systems."""
    with get_db_connection() as conn:
        with conn.cursor() as c:
            # 1. Authentication & RBAC User Table
            c.execute("""
                CREATE TABLE IF NOT EXISTS system_users (
                    username VARCHAR(50) PRIMARY KEY,
                    password_hash VARCHAR(64) NOT NULL,
                    role VARCHAR(20) NOT NULL DEFAULT 'Viewer'
                );
            """)
            
            # Seed default accounts if empty
            c.execute("SELECT COUNT(*) FROM system_users;")
            if c.fetchone()[0] == 0:
                c.execute("INSERT INTO system_users VALUES (%s, %s, %s);", ("admin", hash_password("Admin@Editech2026"), "Admin"))
                c.execute("INSERT INTO system_users VALUES (%s, %s, %s);", ("accountant", hash_password("Finance@2026"), "Accountant"))

            # 2. Audit Trail Log Ledger
            c.execute("""
                CREATE TABLE IF NOT EXISTS system_audit_logs (
                    id SERIAL PRIMARY KEY,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    username VARCHAR(50),
                    action TEXT,
                    module VARCHAR(50)
                );
            """)

            # 3. Dynamic Storefront Multi-Tenant Registers
            c.execute("CREATE TABLE IF NOT EXISTS registered_shops (shop_name VARCHAR(100) PRIMARY KEY);")
            c.execute("CREATE TABLE IF NOT EXISTS shop_keywords (keyword VARCHAR(100) PRIMARY KEY, shop_name VARCHAR(100));")

            # 4. Receivables Subledger (Unpaid Invoices)
            c.execute("""
                CREATE TABLE IF NOT EXISTS active_daily_orders (
                    order_date DATE NOT NULL,
                    order_no VARCHAR(100) PRIMARY KEY,
                    shop_name VARCHAR(100) DEFAULT 'EDITECH DIGITAL',
                    goods_name TEXT,
                    qty INT DEFAULT 1,
                    selling_price NUMERIC(12, 2) DEFAULT 0.00
                );
            """)

            # 5. Suspense Buffer Ledger (Unallocated Bank Receipts)
            c.execute("""
                CREATE TABLE IF NOT EXISTS unkeyed_buffer (
                    order_no VARCHAR(100) PRIMARY KEY,
                    shop_name VARCHAR(100) DEFAULT 'EDITECH DIGITAL',
                    settlement_period VARCHAR(100),
                    complete_amount NUMERIC(12, 2) DEFAULT 0.00,
                    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    order_date DATE NULL
                );
            """)

            # 6. Cleared Historical Book (Paid Invoices Subledger)
            c.execute("""
                CREATE TABLE IF NOT EXISTS historical_archive (
                    order_no VARCHAR(100) PRIMARY KEY,
                    order_date DATE NOT NULL,
                    shop_name VARCHAR(100) DEFAULT 'EDITECH DIGITAL',
                    goods_name TEXT,
                    qty INT,
                    selling_price NUMERIC(12, 2),
                    settlement_period VARCHAR(100),
                    complete_amount NUMERIC(12, 2),
                    commission NUMERIC(12, 2),
                    ds_processing_fee NUMERIC(12, 2),
                    fines NUMERIC(12, 2),
                    other_deductions NUMERIC(12, 2),
                    net_payout NUMERIC(12, 2),
                    archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

def log_audit(username: str, action: str, module: str):
    with get_db_connection() as conn:
        with conn.cursor() as c:
            c.execute("INSERT INTO system_audit_logs (username, action, module) VALUES (%s, %s, %s);", (username, action, module))
