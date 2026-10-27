"""Governance metrics computed from the decisions table and the audit log.

Functions take DataFrames (not a DB handle) so they are pure and easy to test.
Rates are fractions between 0 and 1, or ``None`` when nothing has been reviewed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd
from pydantic import BaseModel

from app.utils import utc_now

RISK_ORDER = ["low", "medium", "high", "critical"]
ACTIONS = ["approve", "reject", "override"]


class GovernanceMetrics(BaseModel):
    total_decisions: int
    pending: int
    reviewed: int
    approved: int
    rejected: int
    overridden: int
    approval_rate: Optional[float]
    rejection_rate: Optional[float]
    override_rate: Optional[float]
    avg_review_seconds: Optional[float]  # time the reviewer spent on the decision screen
    avg_turnaround_seconds: Optional[float]  # submitted -> decided
    oldest_pending_seconds: Optional[float]
    high_risk_pending: int


def _rate(part: int, whole: int) -> Optional[float]:
    return part / whole if whole else None


def _to_float(value: object) -> Optional[float]:
    return None if value is None or pd.isna(value) else float(value)


def _ts(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, format="ISO8601")


def compute_metrics(
    decisions: pd.DataFrame, audit: pd.DataFrame, now: Optional[datetime] = None
) -> GovernanceMetrics:
    now = now or utc_now()
    reviewed = len(audit)
    counts = audit["action"].value_counts()
    approved, rejected, overridden = (int(counts.get(a, 0)) for a in ACTIONS)

    pending_df = decisions[decisions["status"] == "pending"]

    avg_review = _to_float(audit["review_seconds"].dropna().mean()) if reviewed else None

    avg_turnaround = None
    if reviewed:
        merged = audit.merge(
            decisions[["id", "created_at"]], left_on="decision_id", right_on="id", how="left"
        )
        delta = _ts(merged["decided_at"]) - _ts(merged["created_at"])
        avg_turnaround = _to_float(delta.dt.total_seconds().mean())

    oldest_pending = None
    if not pending_df.empty:
        oldest_pending = (now - _ts(pending_df["created_at"]).min()).total_seconds()

    return GovernanceMetrics(
        total_decisions=len(decisions),
        pending=len(pending_df),
        reviewed=reviewed,
        approved=approved,
        rejected=rejected,
        overridden=overridden,
        approval_rate=_rate(approved, reviewed),
        rejection_rate=_rate(rejected, reviewed),
        override_rate=_rate(overridden, reviewed),
        avg_review_seconds=avg_review,
        avg_turnaround_seconds=avg_turnaround,
        oldest_pending_seconds=oldest_pending,
        high_risk_pending=int(pending_df["risk_level"].isin(["high", "critical"]).sum()),
    )


def outcomes_by_risk(audit: pd.DataFrame) -> pd.DataFrame:
    """Long-form counts of each action per risk level (zero-filled)."""
    counts = (
        audit.groupby(["risk_level", "action"]).size().rename("count").reset_index()
        if len(audit)
        else pd.DataFrame(columns=["risk_level", "action", "count"])
    )
    grid = pd.MultiIndex.from_product([RISK_ORDER, ACTIONS], names=["risk_level", "action"])
    full = (
        counts.set_index(["risk_level", "action"])["count"]
        .reindex(grid, fill_value=0)
        .reset_index()
    )
    full["count"] = full["count"].astype(int)
    return full
