"""SQLite data layer: the hospital systems (EMR, scheduling, LIS, billing, pharmacy).

All CSVs in data/raw are loaded as tables on first run. Values are kept as text
exactly as in the CSV (keep_default_na=False, so the sodium test code "NA" is not
read as a missing value). Tools convert numbers where they need to.
"""

import sqlite3
import threading
from contextlib import contextmanager

import pandas as pd
from passlib.hash import pbkdf2_sha256

from app import config

_lock = threading.Lock()
SKIP_TABLES = {"data_dictionary", "golden_set"}


def connect():
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def cursor(write=False):
    conn = connect()
    try:
        if write:
            with _lock:
                yield conn.cursor()
                conn.commit()
        else:
            yield conn.cursor()
    finally:
        conn.close()


def query(sql, params=()):
    with cursor() as cur:
        return [dict(r) for r in cur.execute(sql, params).fetchall()]


def query_one(sql, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql, params=()):
    with cursor(write=True) as cur:
        cur.execute(sql, params)


def init_db(reset=False):
    """Create store/hospital.db from data/raw/*.csv (once), plus users' password hashes and the audit table."""
    config.STORE_DIR.mkdir(parents=True, exist_ok=True)
    if config.DB_PATH.exists() and not reset:
        return
    if config.DB_PATH.exists():
        config.DB_PATH.unlink()

    conn = sqlite3.connect(config.DB_PATH)
    for path in sorted(config.RAW_DIR.glob("*.csv")):
        if path.stem in SKIP_TABLES:
            continue
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        df.to_sql(path.stem, conn, index=False)

    # Demo logins: every user gets DEMO_PASSWORD (hashed, never stored in plain text).
    conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
    demo_hash = pbkdf2_sha256.hash(config.DEMO_PASSWORD)
    conn.execute("UPDATE users SET password_hash = ?", (demo_hash,))

    conn.execute("""
        CREATE TABLE audit_log (
            trace_id TEXT PRIMARY KEY, timestamp TEXT, user_id TEXT, role TEXT, subject_patient_id TEXT,
            question_masked TEXT, intent TEXT, agents TEXT, answer_type TEXT, retrieved_sources TEXT,
            citations TEXT, tools_invoked TEXT, privacy_checks TEXT, guard_flags TEXT, verifier TEXT,
            latency_ms INTEGER, model TEXT, llm_calls INTEGER, error TEXT
        )""")
    for table, col in [("appointments", "appointment_id"), ("doctor_slots", "slot_id"),
                       ("lab_results", "patient_id"), ("orders", "patient_id"), ("billing", "patient_id")]:
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_{col} ON {table}({col})")
    conn.commit()
    conn.close()


def load_golden_set():
    return pd.read_csv(config.RAW_DIR / "golden_set.csv", dtype=str, keep_default_na=False)
