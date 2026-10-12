"""Database helpers: one SQLite connection per request, parameterised queries only."""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from flask import current_app, g, request

WAT = timezone(timedelta(hours=1))  # West Africa Time (Nigeria)


def now() -> str:
    return datetime.now(WAT).strftime("%Y-%m-%d %H:%M:%S")


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        conn = sqlite3.connect(current_app.config["DATABASE"], timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def query(sql, args=(), one=False):
    cur = get_db().execute(sql, args)
    rows = cur.fetchall()
    return (rows[0] if rows else None) if one else rows


def execute(sql, args=()):
    cur = get_db().execute(sql, args)
    return cur.lastrowid


@contextmanager
def transaction():
    """BEGIN IMMEDIATE takes the write lock first, so two officers acting on the
    same request are serialised and the second sees the first one's decision."""
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    try:
        yield db
    except BaseException:
        db.execute("ROLLBACK")
        raise
    else:
        db.execute("COMMIT")


def init_db():
    path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(path, encoding="utf-8") as fh:
        get_db().executescript(fh.read())


def audit(user_id, action, detail=""):
    ip = request.remote_addr if request else None
    execute(
        "INSERT INTO audit_log (user_id, action, detail, ip_address, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, action, detail, ip, now()),
    )
