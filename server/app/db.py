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
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,       -- owner-editable knobs (e.g. trial_days)
            value TEXT NOT NULL
        );
        """
    )
    # Subscription access (local app): a unix time until which the app is unlocked.
    # Added by migration so existing databases upgrade in place.
    cols = [r[1] for r in c.execute("PRAGMA table_info(users)").fetchall()]
    if "access_until" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN access_until REAL NOT NULL DEFAULT 0")
    c.commit()


# Local-app subscription: new accounts get a free trial; a payment extends access.
# The env var is only the FALLBACK — the live value is a setting the owner edits
# in the admin panel, so changing the trial never needs a redeploy or restart.
TRIAL_DAYS = float(os.environ.get("APEXCAM_TRIAL_DAYS", "1"))
DAY = 86400.0


# --- settings (owner-editable, stored in the DB) --------------------------
def get_setting(key: str, default: str = "") -> str:
    c = _connect()
    row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return str(row["value"]) if row else default


def set_setting(key: str, value: str) -> None:
    with _lock:
        c = _connect()
        c.execute("INSERT INTO settings(key,value) VALUES(?,?)"
                  " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
        c.commit()


def trial_days() -> float:
    """Free-trial length for NEW signups: the owner's setting, else the env default."""
    try:
        return float(get_setting("trial_days", "") or TRIAL_DAYS)
    except (TypeError, ValueError):
        return TRIAL_DAYS


# --- users ---------------------------------------------------------------
def create_user(email: str, password_hash: str) -> int:
    """Create an account and start the free trial clock (access_until = now +
    trial_days()). After the trial lapses the app is locked until a payment."""
    now = time.time()
    trial = trial_days()          # read before taking the lock (it queries too)
    with _lock:
        c = _connect()
        cur = c.execute(
            "INSERT INTO users(email, password_hash, created, access_until)"
            " VALUES(?,?,?,?)",
            (email.lower(), password_hash, now, now + trial * DAY))
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


# --- subscription (local-app access) -------------------------------------
def access_until(uid: int) -> float:
    u = get_user(uid)
    return float(u["access_until"]) if u and u["access_until"] is not None else 0.0


def has_subscription_payment(uid: int) -> bool:
    """True once the user has paid at least once (used to tell trial from paid)."""
    c = _connect()
    row = c.execute(
        "SELECT 1 FROM transactions WHERE user_id=? AND kind='subscription' LIMIT 1",
        (uid,)).fetchone()
    return row is not None


def extend_subscription(uid: int, days: float, tx_ref: str, detail: str = "") -> bool:
    """Add `days` of access from a verified payment. Idempotent by tx_ref (a repeated
    callback/webhook can't double-extend). Extends from whichever is later — now or
    the current expiry — so paying early never loses days. Returns False if this
    tx_ref was already applied."""
    now = time.time()
    with _lock:
        c = _connect()
        try:
            c.execute(
                "INSERT INTO transactions(user_id,kind,seconds,detail,tx_ref,created)"
                " VALUES(?,?,?,?,?,?)",
                (uid, "subscription", days * DAY, detail or f"{days:g} days", tx_ref, now))
        except sqlite3.IntegrityError:
            return False   # tx_ref already applied
        row = c.execute("SELECT access_until FROM users WHERE id=?", (uid,)).fetchone()
        base = max(now, float(row["access_until"]) if row and row["access_until"] else 0.0)
        c.execute("UPDATE users SET access_until=? WHERE id=?", (base + days * DAY, uid))
        c.commit()
        return True


# --- reporting (owner panel) ---------------------------------------------
# Read-only views of the business. Sign convention: topup/refund/subscription are
# positive, spend is negative. Admin grants are topups whose tx_ref starts
# 'admin-', which is how comped credit is told apart from real revenue.
def all_users() -> list[sqlite3.Row]:
    return _connect().execute(
        "SELECT id,email,credit_seconds,created,access_until FROM users"
        " ORDER BY created DESC").fetchall()


def recent_transactions(limit: int = 60) -> list[sqlite3.Row]:
    return _connect().execute(
        "SELECT t.kind,t.seconds,t.detail,t.tx_ref,t.created,u.email"
        " FROM transactions t LEFT JOIN users u ON u.id=t.user_id"
        " ORDER BY t.created DESC LIMIT ?", (int(limit),)).fetchall()


def totals() -> dict:
    """Summary of what's been sold, comped, used, and still owed to customers."""
    c = _connect()

    def one(q: str, *args: object) -> float:
        r = c.execute(q, args).fetchone()
        return float(r[0] or 0.0)

    paid = ("SELECT SUM(seconds) FROM transactions WHERE kind='topup'"
            " AND (tx_ref IS NULL OR tx_ref NOT LIKE 'admin-%')")
    granted = ("SELECT SUM(seconds) FROM transactions WHERE kind='topup'"
               " AND tx_ref LIKE 'admin-%'")
    return {
        "users": int(one("SELECT COUNT(*) FROM users")),
        "subscribers": int(one("SELECT COUNT(*) FROM users WHERE access_until > ?",
                               time.time())),
        "outstanding_seconds": one("SELECT SUM(credit_seconds) FROM users"),
        "paid_seconds": one(paid),
        "granted_seconds": one(granted),
        "used_seconds": -one("SELECT SUM(seconds) FROM transactions WHERE kind='spend'"),
    }
