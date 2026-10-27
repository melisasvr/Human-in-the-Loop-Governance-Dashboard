"""Tests for the review workflow, audit-log integrity and metrics."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from app import database as db
from app.models import DecisionStatus, ReviewAction, ReviewSubmission
from app.services import metrics
from app.services.review import (
    DecisionAlreadyReviewedError,
    DecisionNotFoundError,
    InvalidReviewError,
    list_pending,
    submit_review,
)


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    db.init_db(path)
    db.seed_from_json(db.SAMPLE_DATA_PATH, path)
    return path


def _sub(decision_id: str = "DEC-1001", action: ReviewAction = ReviewAction.APPROVE, **kw):
    return ReviewSubmission(decision_id=decision_id, reviewer="alice", action=action, **kw)


# ---- validation ----------------------------------------------------------- #
def test_approve_needs_no_reason():
    assert _sub().reason is None


@pytest.mark.parametrize("action", [ReviewAction.REJECT, ReviewAction.OVERRIDE])
def test_reject_and_override_require_reason(action):
    with pytest.raises(ValidationError, match="reason"):
        _sub(action=action, reason="  short ", final_decision="x")


def test_override_requires_replacement():
    with pytest.raises(ValidationError, match="replacement"):
        _sub(action=ReviewAction.OVERRIDE, reason="Income verified manually")


# ---- review workflow ------------------------------------------------------ #
def test_approve_records_audit_and_updates_status(db_path):
    entry = submit_review(_sub(), review_seconds=12.34, db_path=db_path)
    assert entry.action is ReviewAction.APPROVE
    assert entry.review_seconds == 12.3
    with db.get_connection(db_path) as conn:
        d = db.get_decision(conn, "DEC-1001")
    assert d.status is DecisionStatus.APPROVED
    assert d.final_decision == d.ai_output


def test_reject_has_no_final_decision(db_path):
    entry = submit_review(
        _sub(action=ReviewAction.REJECT, reason="Score ignores recent income change"),
        db_path=db_path,
    )
    assert entry.final_decision is None
    assert entry.reason.startswith("Score ignores")


def test_override_stores_human_decision(db_path):
    entry = submit_review(
        _sub(
            action=ReviewAction.OVERRIDE,
            reason="Verified income documents manually",
            final_decision="Approve loan at $12,000",
        ),
        db_path=db_path,
    )
    assert entry.final_decision == "Approve loan at $12,000"
    with db.get_connection(db_path) as conn:
        assert db.get_decision(conn, "DEC-1001").status is DecisionStatus.OVERRIDDEN


def test_override_identical_to_ai_output_is_rejected(db_path):
    with db.get_connection(db_path) as conn:
        ai_output = db.get_decision(conn, "DEC-1001").ai_output
    with pytest.raises(InvalidReviewError):
        submit_review(
            _sub(action=ReviewAction.OVERRIDE, reason="No real change here", final_decision=ai_output),
            db_path=db_path,
        )


def test_cannot_review_twice(db_path):
    submit_review(_sub(), db_path=db_path)
    with pytest.raises(DecisionAlreadyReviewedError):
        submit_review(_sub(), db_path=db_path)
    with db.get_connection(db_path) as conn:
        assert len(db.fetch_audit_df(conn)) == 1


def test_unknown_decision(db_path):
    with pytest.raises(DecisionNotFoundError):
        submit_review(_sub("DEC-9999"), db_path=db_path)


def test_failed_review_writes_nothing(db_path):
    with pytest.raises(InvalidReviewError):
        with db.get_connection(db_path) as conn:
            ai_output = db.get_decision(conn, "DEC-1001").ai_output
        submit_review(
            _sub(action=ReviewAction.OVERRIDE, reason="Same text as AI", final_decision=ai_output),
            db_path=db_path,
        )
    with db.get_connection(db_path) as conn:
        assert db.get_decision(conn, "DEC-1001").status is DecisionStatus.PENDING
        assert db.fetch_audit_df(conn).empty


def test_queue_orders_by_risk_then_age(db_path):
    queue = list_pending(db_path)
    ranks = [d.risk_level.rank for d in queue]
    assert ranks == sorted(ranks, reverse=True)
    assert queue[0].risk_level.value == "critical"


# ---- audit log integrity --------------------------------------------------- #
def test_audit_log_is_append_only(db_path):
    submit_review(_sub(), db_path=db_path)
    with db.get_connection(db_path) as conn:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("UPDATE audit_log SET reason = 'tampered'")
    with db.get_connection(db_path) as conn:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("DELETE FROM audit_log")


# ---- seeding --------------------------------------------------------------- #
def test_seed_is_idempotent(db_path):
    assert db.seed_from_json(db.SAMPLE_DATA_PATH, db_path) == 0
    assert db.seed_if_empty(db.SAMPLE_DATA_PATH, db_path) == 0


# ---- metrics ---------------------------------------------------------------- #
def _frames(db_path):
    with db.get_connection(db_path) as conn:
        return db.fetch_decisions_df(conn), db.fetch_audit_df(conn)


def test_metrics_with_no_reviews(db_path):
    m = metrics.compute_metrics(*_frames(db_path))
    assert m.reviewed == 0 and m.pending == 13
    assert m.approval_rate is None and m.avg_review_seconds is None
    assert m.oldest_pending_seconds > 0


def test_metrics_rates(db_path):
    submit_review(_sub("DEC-1001"), review_seconds=30, db_path=db_path)
    submit_review(_sub("DEC-1002"), review_seconds=60, db_path=db_path)
    submit_review(
        _sub("DEC-1003", ReviewAction.REJECT, reason="Symptoms warrant higher priority"),
        db_path=db_path,
    )
    submit_review(
        _sub("DEC-1004", ReviewAction.OVERRIDE, reason="Customer confirmed the purchase",
             final_decision="Allow transaction"),
        review_seconds=90,
        db_path=db_path,
    )
    decisions, audit = _frames(db_path)
    m = metrics.compute_metrics(decisions, audit)
    assert (m.reviewed, m.approved, m.rejected, m.overridden) == (4, 2, 1, 1)
    assert m.approval_rate == 0.5 and m.rejection_rate == 0.25 and m.override_rate == 0.25
    assert m.avg_review_seconds == 60
    assert m.pending == 9
    assert m.avg_turnaround_seconds > 0

    by_risk = metrics.outcomes_by_risk(audit)
    assert len(by_risk) == 12  # 4 risk levels x 3 actions, zero-filled
    assert by_risk["count"].sum() == 4
