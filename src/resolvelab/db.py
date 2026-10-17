"""SQLite schema and connection helper. Money is stored as integer cents."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    tier  TEXT NOT NULL CHECK (tier IN ('standard', 'silver', 'gold'))
);

CREATE TABLE IF NOT EXISTS orders (
    id               TEXT PRIMARY KEY,
    customer_id      INTEGER NOT NULL REFERENCES customers(id),
    status           TEXT NOT NULL CHECK (status IN ('processing', 'shipped', 'delivered', 'cancelled')),
    placed_on        TEXT NOT NULL,
    shipped_on       TEXT,
    delivered_on     TEXT,
    shipping_address TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS order_items (
    id               INTEGER PRIMARY KEY,
    order_id         TEXT NOT NULL REFERENCES orders(id),
    sku              TEXT NOT NULL,
    name             TEXT NOT NULL,
    category         TEXT NOT NULL,
    qty              INTEGER NOT NULL CHECK (qty > 0),
    unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0)
);

CREATE TABLE IF NOT EXISTS refunds (
    id           INTEGER PRIMARY KEY,
    order_id     TEXT NOT NULL REFERENCES orders(id),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    reason       TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('processed', 'pending_approval', 'rejected')),
    requested_at TEXT NOT NULL,
    decided_by   TEXT
);

CREATE TABLE IF NOT EXISTS escalations (
    id             INTEGER PRIMARY KEY,
    order_id       TEXT REFERENCES orders(id),
    customer_email TEXT NOT NULL,
    summary        TEXT NOT NULL,
    priority       TEXT NOT NULL CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
    status         TEXT NOT NULL DEFAULT 'open',
    created_at     TEXT NOT NULL
);

-- Every tool call lands here, allowed or denied. The eval lab and the UI
-- action timeline both read from this table.
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY,
    ts          TEXT NOT NULL,
    tool        TEXT NOT NULL,
    args_json   TEXT NOT NULL,
    outcome     TEXT NOT NULL CHECK (outcome IN ('ok', 'denied', 'error')),
    result_json TEXT NOT NULL
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a connection. check_same_thread is off because the MCP server may run
    sync tools on a worker thread; SupportService serialises access with a lock."""
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
