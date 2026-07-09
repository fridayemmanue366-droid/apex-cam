"""Apex Cam cloud — SQLite store (users, credits ledger, transactions).

The credit balance lives HERE on the server, not on the customer's PC, so it
can't be edited to grant free usage. SQLite to start (one file, zero setup);
swap the DSN for Postgres in production without changing the callers.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path

DB_PATH = Path(os.environ.get("APEXCAM_DB", "apexcam.db"))
_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _init(_conn)
    return _conn


def _init(c: sqlite3.Connection) -> None:
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            email         TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            credit_seconds REAL NOT NULL DEFAULT 0,
            created       REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id  INTEGER NOT NULL,
            kind     TEXT NOT NULL,          -- topup | spend | refund
            seconds  REAL NOT NULL,          -- +credit / -debit (wallet seconds)
            detail   TEXT,
            tx_ref   TEXT,
            created  REAL NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ix_tx_ref ON transactions(tx_ref)
            WHERE tx_ref IS NOT NULL;
        """
    )
    c.commit()


# --- users ---------------------------------------------------------------
def create_user(email: str, password_hash: str) -> int:
    with _lock:
        c = _connect()
        cur = c.execute(
            "INSERT INTO users(email, password_hash, created) VALUES(?,?,?)",
            (email.lower(), password_hash, time.time()))
        c.commit()
        return int(cur.lastrowid)


def get_user_by_email(email: str) -> sqlite3.Row | None:
    c = _connect()
    return c.execute("SELECT * FROM users WHERE email=?", (email.lower(),)).fetchone()


def get_user(uid: int) -> sqlite3.Row | None:
    c = _connect()
    return c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


# --- credits (all changes go through the ledger) -------------------------
def credit_seconds(uid: int) -> float:
    u = get_user(uid)
    return float(u["credit_seconds"]) if u else 0.0


def topup(uid: int, seconds: float, tx_ref: str, detail: str = "") -> bool:
    """Add credit from a verified payment. Idempotent by tx_ref (a repeated
    webhook can't double-credit). Returns False if this tx_ref was already used."""
    with _lock:
        c = _connect()
        try:
            c.execute(
                "INSERT INTO transactions(user_id,kind,seconds,detail,tx_ref,created)"
                " VALUES(?,?,?,?,?,?)",
                (uid, "topup", seconds, detail, tx_ref, time.time()))
        except sqlite3.IntegrityError:
            return False   # tx_ref already applied
        c.execute("UPDATE users SET credit_seconds = credit_seconds + ? WHERE id=?",
                  (seconds, uid))
        c.commit()
        return True


def spend(uid: int, seconds: float, detail: str = "") -> bool:
    """Debit the wallet for usage. Returns False (no debit) if the balance can't
    cover it — this is what stops a customer using more than they paid for."""
    seconds = max(0.0, float(seconds))
    with _lock:
        c = _connect()
        row = c.execute("SELECT credit_seconds FROM users WHERE id=?", (uid,)).fetchone()
        if not row or float(row["credit_seconds"]) < seconds:
            return False
        c.execute("UPDATE users SET credit_seconds = credit_seconds - ? WHERE id=?",
                  (seconds, uid))
        c.execute("INSERT INTO transactions(user_id,kind,seconds,detail,created)"
                  " VALUES(?,?,?,?,?)", (uid, "spend", -seconds, detail, time.time()))
        c.commit()
        return True


def refund(uid: int, seconds: float, detail: str = "") -> None:
    with _lock:
        c = _connect()
        c.execute("UPDATE users SET credit_seconds = credit_seconds + ? WHERE id=?",
                  (seconds, uid))
        c.execute("INSERT INTO transactions(user_id,kind,seconds,detail,created)"
                  " VALUES(?,?,?,?,?)", (uid, "refund", seconds, detail, time.time()))
        c.commit()
