"""SQLite setup and data-access helpers.

Design notes
------------
* ``decisions`` holds the AI outputs and their current status.
* ``audit_log`` is **append-only**: database triggers abort any UPDATE or DELETE,
  so history cannot be edited through the application.
* Every audit row stores a snapshot of the AI output and risk metadata at the
  moment of review, so the record stays meaningful on its own.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from pydantic import ValidationError

from app.models import AIDecision, DecisionStatus
from app.utils import utc_now

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = Path(os.environ.get("HITL_DB_PATH", PROJECT_ROOT / "data" / "governance.db"))
SAMPLE_DATA_PATH = PROJECT_ROOT / "data" / "sample_decisions.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    category        TEXT NOT NULL,
    ai_output       TEXT NOT NULL,
    confidence      REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    risk_level      TEXT NOT NULL CHECK (risk_level IN ('low','medium','high','critical')),
    context         TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','approved','rejected','overridden')),
    final_decision  TEXT,
    created_at      TEXT NOT NULL,
    reviewed_at     TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id     TEXT NOT NULL REFERENCES decisions(id),
    decision_title  TEXT NOT NULL,
    category        TEXT NOT NULL,
    risk_level      TEXT NOT NULL,
    confidence      REAL,
    ai_output       TEXT NOT NULL,
    reviewer        TEXT NOT NULL,
    action          TEXT NOT NULL CHECK (action IN ('approve','reject','override')),
    reason          TEXT,
    final_decision  TEXT,
    review_seconds  REAL,
    decided_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_decisions_status ON decisions(status);
CREATE INDEX IF NOT EXISTS idx_audit_decision ON audit_log(decision_id);

CREATE TRIGGER IF NOT EXISTS audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only: updates are not allowed');
END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only: deletes are not allowed');
END;
"""


@contextmanager
def get_connection(db_path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    """Yield a connection that commits on success and rolls back on any error."""
    conn = sqlite3.connect(str(db_path or DEFAULT_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path | str | None = None) -> None:
    """Create tables, indexes and append-only triggers (idempotent)."""
    path = Path(db_path or DEFAULT_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection(path) as conn:
        conn.executescript(SCHEMA)


def reset_database(db_path: Path | str | None = None) -> None:
    """Delete the database file and recreate it. Demo use only."""
    path = Path(db_path or DEFAULT_DB_PATH)
    path.unlink(missing_ok=True)
    init_db(path)


# --------------------------------------------------------------------------- #
# Decisions
# --------------------------------------------------------------------------- #
def _row_to_decision(row: sqlite3.Row) -> AIDecision:
    data: dict[str, Any] = dict(row)
    data["context"] = json.loads(data["context"] or "{}")
    return AIDecision.model_validate(data)


def insert_decision(conn: sqlite3.Connection, decision: AIDecision) -> bool:
    """Insert a decision; returns False if the id already exists."""
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO decisions
            (id, title, category, ai_output, confidence, risk_level, context,
             status, final_decision, created_at, reviewed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            decision.id,
            decision.title,
            decision.category,
            decision.ai_output,
            decision.confidence,
            decision.risk_level.value,
            json.dumps(decision.context, ensure_ascii=False),
            decision.status.value,
            decision.final_decision,
            decision.created_at.isoformat(),
            decision.reviewed_at.isoformat() if decision.reviewed_at else None,
        ),
    )
    return cur.rowcount == 1


def get_decision(conn: sqlite3.Connection, decision_id: str) -> Optional[AIDecision]:
    row = conn.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone()
    return _row_to_decision(row) if row else None


def list_decisions(
    conn: sqlite3.Connection, status: Optional[DecisionStatus] = None
) -> list[AIDecision]:
    if status is None:
        rows = conn.execute("SELECT * FROM decisions ORDER BY created_at").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM decisions WHERE status = ? ORDER BY created_at", (status.value,)
        ).fetchall()
    return [_row_to_decision(r) for r in rows]


def mark_decision_reviewed(
    conn: sqlite3.Connection,
    decision_id: str,
    status: DecisionStatus,
    final_decision: Optional[str],
    reviewed_at: str,
) -> bool:
    """Move a *pending* decision to its final status.

    The ``status = 'pending'`` guard makes the update atomic: if two reviewers
    submit at once, only one succeeds and the other gets ``False``.
    """
    cur = conn.execute(
        """
        UPDATE decisions
           SET status = ?, final_decision = ?, reviewed_at = ?
         WHERE id = ? AND status = 'pending'
        """,
        (status.value, final_decision, reviewed_at, decision_id),
    )
    return cur.rowcount == 1


def count_decisions(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0])


def fetch_decisions_df(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM decisions ORDER BY created_at", conn)


# --------------------------------------------------------------------------- #
# Audit log
# --------------------------------------------------------------------------- #
def insert_audit_entry(
    conn: sqlite3.Connection,
    *,
    decision: AIDecision,
    reviewer: str,
    action: str,
    reason: Optional[str],
    final_decision: Optional[str],
    review_seconds: Optional[float],
    decided_at: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO audit_log
            (decision_id, decision_title, category, risk_level, confidence, ai_output,
             reviewer, action, reason, final_decision, review_seconds, decided_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            decision.id,
            decision.title,
            decision.category,
            decision.risk_level.value,
            decision.confidence,
            decision.ai_output,
            reviewer,
            action,
            reason,
            final_decision,
            review_seconds,
            decided_at,
        ),
    )
    return int(cur.lastrowid)


def get_audit_row(conn: sqlite3.Connection, entry_id: int) -> sqlite3.Row:
    return conn.execute("SELECT * FROM audit_log WHERE id = ?", (entry_id,)).fetchone()


def fetch_audit_df(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT * FROM audit_log ORDER BY decided_at DESC, id DESC", conn
    )


# --------------------------------------------------------------------------- #
# Sample data
# --------------------------------------------------------------------------- #
def seed_from_json(
    path: Path | str = SAMPLE_DATA_PATH, db_path: Path | str | None = None
) -> int:
    """Load decisions from a JSON file. Existing ids are skipped. Returns rows inserted.

    ``submitted_hours_ago`` (optional) back-dates ``created_at`` so the demo queue
    looks realistic whenever it is loaded.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path} must contain a JSON list of decisions.")

    now = utc_now()
    inserted = 0
    with get_connection(db_path) as conn:
        for item in raw:
            item = dict(item)
            hours_ago = float(item.pop("submitted_hours_ago", 0))
            try:
                decision = AIDecision(**item, created_at=now - timedelta(hours=hours_ago))
            except ValidationError as exc:
                raise ValueError(f"Invalid decision '{item.get('id', '?')}': {exc}") from exc
            inserted += insert_decision(conn, decision)
    return inserted


def seed_if_empty(
    path: Path | str = SAMPLE_DATA_PATH, db_path: Path | str | None = None
) -> int:
    """Seed sample data only when the decisions table is empty."""
    with get_connection(db_path) as conn:
        if count_decisions(conn) > 0:
            return 0
    if not Path(path).exists():
        return 0
    return seed_from_json(path, db_path)
