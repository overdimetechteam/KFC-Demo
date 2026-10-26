"""SQLite storage for users and match history."""
import sqlite3

from flask import current_app, g
from werkzeug.security import generate_password_hash

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    full_name     TEXT,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',      -- 'admin' or 'user'
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    last_login    TEXT
);
CREATE TABLE IF NOT EXISTS match_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    po_no       TEXT,
    invoice_no  TEXT,
    grn_no      TEXT,
    overall     TEXT NOT NULL,
    fails       INTEGER NOT NULL,
    warns       INTEGER NOT NULL,
    result_json TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS payment_vouchers (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    voucher_no     TEXT UNIQUE,
    user_id        INTEGER NOT NULL REFERENCES users(id),
    run_id         INTEGER NOT NULL REFERENCES match_runs(id),
    po_no          TEXT NOT NULL,
    invoice_nos    TEXT NOT NULL,
    grn_nos        TEXT NOT NULL,
    supplier_code  TEXT,
    supplier_name  TEXT,
    supplier_tin   TEXT,
    currency       TEXT,
    supply_total   REAL,
    discount_total REAL,
    vat_total      REAL,
    cess_total     REAL,
    net_payable    REAL NOT NULL,
    match_result   TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'PREPARED',
    detail_json    TEXT NOT NULL,                     -- documents as they were when the voucher was raised
    created_at     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS payment_voucher_invoices (
    voucher_id INTEGER NOT NULL REFERENCES payment_vouchers(id),
    user_id    INTEGER NOT NULL,
    invoice_no TEXT NOT NULL,
    UNIQUE (user_id, invoice_no)                      -- an invoice is paid on one voucher only
);
CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER,
    action     TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
"""


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(app):
    with app.app_context():
        db = get_db()
        db.executescript(SCHEMA)
        if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            user = app.config["DEFAULT_ADMIN_USER"]
            pwd = app.config["DEFAULT_ADMIN_PASSWORD"]
            db.execute(
                "INSERT INTO users (username, full_name, password_hash, role) VALUES (?, ?, ?, 'admin')",
                (user, "Administrator", generate_password_hash(pwd)),
            )
            app.logger.warning("Created default admin account '%s' — change its password after first login.", user)
        db.commit()
    app.teardown_appcontext(close_db)


def audit(user_id, action, detail=""):
    db = get_db()
    db.execute("INSERT INTO audit_log (user_id, action, detail) VALUES (?, ?, ?)", (user_id, action, detail))
    db.commit()
