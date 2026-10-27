"""Pydantic data models and enums for decisions, reviews and audit entries."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.utils import utc_now

MIN_REASON_LENGTH = 10
LOW_CONFIDENCE_THRESHOLD = 0.60


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        """Higher number = more urgent. Used to order the review queue."""
        return {"low": 0, "medium": 1, "high": 2, "critical": 3}[self.value]


class DecisionStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    OVERRIDDEN = "overridden"


class ReviewAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    OVERRIDE = "override"

    @property
    def resulting_status(self) -> DecisionStatus:
        return {
            "approve": DecisionStatus.APPROVED,
            "reject": DecisionStatus.REJECTED,
            "override": DecisionStatus.OVERRIDDEN,
        }[self.value]

    @property
    def requires_reason(self) -> bool:
        return self is not ReviewAction.APPROVE


class AIDecision(BaseModel):
    """An AI output that is waiting for (or has received) human review."""

    model_config = ConfigDict(str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=80)
    ai_output: str = Field(min_length=1)
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    risk_level: RiskLevel
    context: dict[str, Any] = Field(default_factory=dict)
    status: DecisionStatus = DecisionStatus.PENDING
    final_decision: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    reviewed_at: Optional[datetime] = None

    @property
    def is_low_confidence(self) -> bool:
        return self.confidence is not None and self.confidence < LOW_CONFIDENCE_THRESHOLD


class ReviewSubmission(BaseModel):
    """A reviewer's verdict. Validation enforces the governance rules."""

    model_config = ConfigDict(str_strip_whitespace=True)

    decision_id: str = Field(min_length=1)
    reviewer: str = Field(min_length=2, max_length=80)
    action: ReviewAction
    reason: Optional[str] = None
    final_decision: Optional[str] = None

    @model_validator(mode="after")
    def _enforce_governance_rules(self) -> "ReviewSubmission":
        if self.action.requires_reason and (
            not self.reason or len(self.reason) < MIN_REASON_LENGTH
        ):
            raise ValueError(
                f"A reason of at least {MIN_REASON_LENGTH} characters is required "
                f"to {self.action.value} a decision."
            )
        if self.action is ReviewAction.OVERRIDE and not self.final_decision:
            raise ValueError("Enter the replacement decision to override the AI output.")
        return self


class AuditEntry(BaseModel):
    """One immutable row of the audit log (includes a snapshot of the AI output)."""

    id: int
    decision_id: str
    decision_title: str
    category: str
    risk_level: RiskLevel
    confidence: Optional[float] = None
    ai_output: str
    reviewer: str
    action: ReviewAction
    reason: Optional[str] = None
    final_decision: Optional[str] = None
    review_seconds: Optional[float] = None
    decided_at: datetime
