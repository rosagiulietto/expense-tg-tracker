"""
Database layer for the Telegram Expense Tracker Bot.

Phase 1: Database Schema
    - SQLite database file: expenses.db
    - Single relational table: expenses
        id        INTEGER  Primary Key, Auto-increment
        timestamp TEXT     DATETIME (ISO 8601, UTC)
        item      TEXT     Expense description
        amount    REAL     Expense amount
        category  TEXT     Default: "General"
        user_id   INTEGER  Telegram user id (security/authorization)
"""

import sqlite3
from datetime import date as date_type
from datetime import datetime, time, timedelta, timezone
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

DB_NAME = "expenses.db"

# All timestamps are stored in UTC. Day/month boundaries are computed in this
# timezone so that "today" and "this month" match the user's local calendar.
LOCAL_TZ = ZoneInfo("Europe/Rome")

_DEFAULT_CATEGORY = "General"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS expenses (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    item      TEXT NOT NULL,
    amount    REAL NOT NULL,
    category  TEXT NOT NULL DEFAULT 'General',
    user_id   INTEGER NOT NULL
);
"""

CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_expenses_timestamp ON expenses (timestamp);
CREATE INDEX IF NOT EXISTS idx_expenses_user_id ON expenses (user_id);
"""


def get_connection(db_path: str = DB_NAME) -> sqlite3.Connection:
    """Open a connection to the SQLite database, ensuring the schema exists."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(_SCHEMA)
    conn.executescript(CREATE_INDEX)
    conn.commit()
    return conn


def init_db(db_path: str = DB_NAME) -> None:
    """Create the database file and tables if they don't exist yet."""
    conn = get_connection(db_path)
    conn.close()


def _utc_now_iso() -> str:
    """Current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _as_utc_iso(dt: datetime) -> str:
    """Convert an aware datetime to a UTC ISO 8601 string."""
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _day_bounds_utc(day: date_type) -> Tuple[str, str]:
    """Return [start, end) UTC ISO strings for a local calendar day."""
    start_local = datetime.combine(day, time.min, tzinfo=LOCAL_TZ)
    end_local = start_local + timedelta(days=1)
    return _as_utc_iso(start_local), _as_utc_iso(end_local)


def _month_bounds_utc(year: int, month: int) -> Tuple[str, str]:
    """Return [start, end) UTC ISO strings for a local calendar month."""
    start_local = datetime(year, month, 1, tzinfo=LOCAL_TZ)
    if month == 12:
        end_local = datetime(year + 1, 1, 1, tzinfo=LOCAL_TZ)
    else:
        end_local = datetime(year, month + 1, 1, tzinfo=LOCAL_TZ)
    return _as_utc_iso(start_local), _as_utc_iso(end_local)


# ---------------------------------------------------------------------------
# CRUD helpers (used by Phase 2 handlers)
# ---------------------------------------------------------------------------

def add_expense(
    item: str,
    amount: float,
    user_id: int,
    category: str = _DEFAULT_CATEGORY,
    timestamp: Optional[str] = None,
    db_path: str = DB_NAME,
) -> int:
    """Insert a new expense row and return its id."""
    conn = get_connection(db_path)
    cur = conn.execute(
        "INSERT INTO expenses (timestamp, item, amount, category, user_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (timestamp or _utc_now_iso(), item, amount, category, user_id),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def get_last_expenses(n: int, user_id: int, db_path: str = DB_NAME) -> List[sqlite3.Row]:
    """Return the last `n` expenses for the given user, newest first."""
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM expenses WHERE user_id = ? ORDER BY timestamp DESC, id DESC LIMIT ?",
        (user_id, n),
    ).fetchall()
    conn.close()
    return rows


def get_all_expenses(user_id: int, db_path: str = DB_NAME) -> List[sqlite3.Row]:
    """Return all expenses for the given user, oldest first (for CSV export)."""
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM expenses WHERE user_id = ? ORDER BY timestamp ASC, id ASC",
        (user_id,),
    ).fetchall()
    conn.close()
    return rows


def delete_expense(expense_id: int, user_id: int, db_path: str = DB_NAME) -> bool:
    """Delete an expense by id for the given user. Returns True if a row was removed."""
    conn = get_connection(db_path)
    cur = conn.execute(
        "DELETE FROM expenses WHERE id = ? AND user_id = ?",
        (expense_id, user_id),
    )
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def daily_total(user_id: int, date: Optional[str] = None, db_path: str = DB_NAME) -> float:
    """
    Return total spending for a local calendar day (YYYY-MM-DD, Europe/Rome).
    Defaults to today in the local timezone.
    """
    if date is None:
        local_day = datetime.now(LOCAL_TZ).date()
    else:
        local_day = date_type.fromisoformat(date)
    start_utc, end_utc = _day_bounds_utc(local_day)
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM expenses "
        "WHERE user_id = ? AND timestamp >= ? AND timestamp < ?",
        (user_id, start_utc, end_utc),
    ).fetchone()
    conn.close()
    return row["total"]


def monthly_summary(
    user_id: int,
    year: int,
    month: int,
    db_path: str = DB_NAME,
) -> Tuple[float, List[sqlite3.Row]]:
    """
    Return (total, per-category breakdown) for the given local calendar month
    (Europe/Rome). `breakdown` is a list of rows with columns: category, total.
    """
    start_utc, end_utc = _month_bounds_utc(year, month)
    conn = get_connection(db_path)

    total_row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM expenses "
        "WHERE user_id = ? AND timestamp >= ? AND timestamp < ?",
        (user_id, start_utc, end_utc),
    ).fetchone()

    breakdown = conn.execute(
        "SELECT category, SUM(amount) AS total FROM expenses "
        "WHERE user_id = ? AND timestamp >= ? AND timestamp < ? "
        "GROUP BY category ORDER BY total DESC",
        (user_id, start_utc, end_utc),
    ).fetchall()
    conn.close()
    return total_row["total"], breakdown


if __name__ == "__main__":
    # Manual test: python database.py
    init_db()
    print(f"Database '{DB_NAME}' initialized with 'expenses' table.")
