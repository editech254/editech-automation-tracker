# database.py
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
import bcrypt

# Postgres connection string. Provide via DATABASE_URL env var in production.
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/editech_db",
)


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


# --- Password hashing (bcrypt, salted) -------------------------------------
def hash_password(password: str) -> str:
    """Salted bcrypt hash. Stored as utf-8 string."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, stored_hash: str) -> bool:
    """Constant-time verify against a stored bcrypt hash."""
    if not password or not stored_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# --- Schema ----------------------------------------------------------------
def init_db():
    """Create schema. NEVER seeds default credentials.

    First-launch admin bootstrap is handled in app.py via an interactive
    setup form, so no plaintext passwords ever live in source control.
    """
    with get_db_connection() as conn:
        with conn.cursor() as c:
            # bcrypt hashes are ~60 chars; allow 255 for future algos.
            c.execute("""
                CREATE TABLE IF NOT EXISTS system_users (
                    username VARCHAR(50) PRIMARY KEY,
                    password_hash VARCHAR(255) NOT NULL,
                    role VARCHAR(20) NOT NULL DEFAULT 'Viewer',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            c.execute("""
                CREATE TABLE IF NOT EXISTS system_audit_logs (
                    id SERIAL PRIMARY KEY,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    username VARCHAR(50),
                    action TEXT,
                    module VARCHAR(50)
                );
            """)

            c.execute("CREATE TABLE IF NOT EXISTS registered_shops (shop_name VARCHAR(100) PRIMARY KEY);")
            c.execute("CREATE TABLE IF NOT EXISTS shop_keywords (keyword VARCHAR(100) PRIMARY KEY, shop_name VARCHAR(100));")

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


def has_any_user() -> bool:
    """True once at least one account exists. Drives first-launch wizard."""
    with get_db_connection() as conn:
        with conn.cursor() as c:
            c.execute("SELECT COUNT(*) FROM system_users;")
            return c.fetchone()[0] > 0


def create_user(username: str, password: str, role: str = "Admin") -> None:
    """Insert a user with a freshly-salted bcrypt hash."""
    with get_db_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "INSERT INTO system_users (username, password_hash, role) VALUES (%s, %s, %s);",
                (username, hash_password(password), role),
            )


def get_user(username: str):
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute("SELECT * FROM system_users WHERE username = %s", (username,))
            return c.fetchone()


def log_audit(username: str, action: str, module: str) -> None:
    with get_db_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "INSERT INTO system_audit_logs (username, action, module) VALUES (%s, %s, %s);",
                (username, action, module),
            )
