"""Minimal SQLite loader (stand-in for Person 1's db.py). Loads every CSV in data/raw as a table."""

import sqlite3

import pandas as pd

from app import config


def connect():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def query(sql, params=()):
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def query_one(sql, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql, params=()):
    with connect() as conn:
        conn.execute(sql, params)


def init_db(reset=True):
    config.DB_PATH.parent.mkdir(exist_ok=True)
    if config.DB_PATH.exists() and reset:
        config.DB_PATH.unlink()
    with sqlite3.connect(config.DB_PATH) as conn:
        for path in sorted(config.RAW_DIR.glob("*.csv")):
            if path.stem not in ("data_dictionary", "golden_set"):
                pd.read_csv(path, dtype=str, keep_default_na=False).to_sql(path.stem, conn, index=False,
                                                                          if_exists="replace")
