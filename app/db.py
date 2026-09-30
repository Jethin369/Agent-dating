import os
import sqlite3
import threading

DB_PATH = os.getenv("DB_PATH", "data/app.db")
_lock = threading.Lock()
_conn = None

SCHEMA = """
create table if not exists people (
    id integer primary key autoincrement,
    name text default '',
    linkedin_url text unique,
    instagram_url text,
    raw text,
    profile text,
    status text default 'working',
    error text
);
create table if not exists pairs (
    a integer, b integer,
    score_ab real, score_ba real, note text,
    primary key (a, b)
);
create table if not exists dates (
    id integer primary key autoincrement,
    a integer, b integer,
    transcript text, verdict_a text, verdict_b text,
    unique (a, b)
);
"""


def init():
    global _conn
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.executescript(SCHEMA)
    _conn.commit()


def run(sql, args=()):
    with _lock:
        cur = _conn.execute(sql, args)
        _conn.commit()
        return cur.lastrowid


def all(sql, args=()):
    with _lock:
        return [dict(r) for r in _conn.execute(sql, args).fetchall()]


def one(sql, args=()):
    rows = all(sql, args)
    return rows[0] if rows else None
