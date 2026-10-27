"""Approve / Reject / Override business logic.

A review is one atomic transaction: the decision's status is updated *and* an
audit row is written, or neither happens.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from app import database as db
from app.models import (
    AIDecision,
    AuditEntry,
    DecisionStatus,
    ReviewAction,
    ReviewSubmission,
)
from app.utils import utc_now_iso


class ReviewError(Exception):
    """Base class for review failures that should be shown to the user."""


class DecisionNotFoundError(ReviewError):
    pass


class DecisionAlreadyReviewedError(ReviewError):
    pass


class InvalidReviewError(ReviewError):
    pass


def list_pending(db_path: Path | str | None = None) -> list[AIDecision]:
    """Pending decisions, most urgent first (risk, then oldest)."""
    with db.get_connection(db_path) as conn:
        pending = db.list_decisions(conn, status=DecisionStatus.PENDING)
    return sorted(pending, key=lambda d: (-d.risk_level.rank, d.created_at))


def get_decision(decision_id: str, db_path: Path | str | None = None) -> Optional[AIDecision]:
    with db.get_connection(db_path) as conn:
        return db.get_decision(conn, decision_id)


def _resolve_final_decision(decision: AIDecision, submission: ReviewSubmission) -> Optional[str]:
    """What actually happens as a result of the review."""
    if submission.action is ReviewAction.APPROVE:
        return decision.ai_output
    if submission.action is ReviewAction.REJECT:
        return None  # AI output not adopted; no replacement recorded
    replacement = submission.final_decision or ""
    if replacement.strip().casefold() == decision.ai_output.strip().casefold():
        raise InvalidReviewError(
            "The replacement is identical to the AI output. Use Approve instead, "
            "or change the decision text."
        )
    return replacement


def submit_review(
    submission: ReviewSubmission,
    review_seconds: Optional[float] = None,
    db_path: Path | str | None = None,
) -> AuditEntry:
    """Apply a human verdict and write the audit entry. Returns the saved entry."""
    decided_at = utc_now_iso()
    if review_seconds is not None:
        review_seconds = round(max(0.0, review_seconds), 1)

    with db.get_connection(db_path) as conn:
        decision = db.get_decision(conn, submission.decision_id)
        if decision is None:
            raise DecisionNotFoundError(f"Decision {submission.decision_id} does not exist.")
        if decision.status is not DecisionStatus.PENDING:
            raise DecisionAlreadyReviewedError(
                f"{decision.id} was already {decision.status.value}. Refresh the inbox."
            )

        final_decision = _resolve_final_decision(decision, submission)

        updated = db.mark_decision_reviewed(
            conn,
            decision.id,
            submission.action.resulting_status,
            final_decision,
            decided_at,
        )
        if not updated:  # lost a race with another reviewer
            raise DecisionAlreadyReviewedError(
                f"{decision.id} was reviewed by someone else. Refresh the inbox."
            )

        entry_id = db.insert_audit_entry(
            conn,
            decision=decision,
            reviewer=submission.reviewer,
            action=submission.action.value,
            reason=submission.reason or None,
            final_decision=final_decision,
            review_seconds=review_seconds,
            decided_at=decided_at,
        )
        row = db.get_audit_row(conn, entry_id)
        return AuditEntry.model_validate(dict(row))
