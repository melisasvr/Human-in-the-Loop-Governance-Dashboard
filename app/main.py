"""Human-in-the-Loop Governance Dashboard (Streamlit).

Run from the project root:  streamlit run app/main.py
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from html import escape
from pathlib import Path

# Make ``import app...`` work when Streamlit runs this file as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from app import database as db
from app.models import AIDecision, ReviewAction, ReviewSubmission
from app.services import metrics as metrics_service
from app.services.review import ReviewError, get_decision, list_pending, submit_review
from app.utils import (
    dataframe_to_csv_bytes,
    dataframe_to_json_bytes,
    format_duration,
    format_percent,
    format_timestamp,
    format_validation_error,
    risk_badge_html,
    time_ago,
    truncate,
    utc_now,
)

PAGES = ["Decision Inbox", "Review", "Audit Log", "Governance Metrics"]
REPORTS_DIR = db.PROJECT_ROOT / "reports"
ACTION_LABELS = {
    "Approve": ReviewAction.APPROVE,
    "Reject": ReviewAction.REJECT,
    "Override": ReviewAction.OVERRIDE,
}
ACTION_HELP = {
    ReviewAction.APPROVE: "Accept the AI output as the final decision.",
    ReviewAction.REJECT: "Do not use the AI output. A reason is required.",
    ReviewAction.OVERRIDE: "Replace the AI output with your own decision. A reason is required.",
}
OUTCOME_COLORS = {"approve": "#2E7D32", "reject": "#C62828", "override": "#EF6C00"}

st.set_page_config(page_title="HITL Governance Dashboard", page_icon="🛡️", layout="wide")


# --------------------------------------------------------------------------- #
# State helpers
# --------------------------------------------------------------------------- #
def bootstrap() -> None:
    """Create the database and load sample data once per session."""
    if st.session_state.get("_bootstrapped"):
        return
    db.init_db()
    db.seed_if_empty()
    st.session_state["_bootstrapped"] = True


def go_to(page: str, decision_id: str | None = None) -> None:
    """Queue a navigation change; applied before the nav widget is created."""
    st.session_state["_nav_target"] = page
    if decision_id is not None:
        st.session_state["selected_decision"] = decision_id


def open_review(decision_id: str) -> None:  # used as a button callback
    go_to("Review", decision_id)


def flash(kind: str, message: str) -> None:
    st.session_state["_flash"] = (kind, message)


def show_flash() -> None:
    item = st.session_state.pop("_flash", None)
    if item:
        kind, message = item
        {"success": st.success, "error": st.error}.get(kind, st.info)(message)


def load_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    with db.get_connection() as conn:
        return db.fetch_decisions_df(conn), db.fetch_audit_df(conn)


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
def render_sidebar() -> str:
    if "_nav_target" in st.session_state:
        st.session_state["page"] = st.session_state.pop("_nav_target")
    st.session_state.setdefault("page", PAGES[0])

    with st.sidebar:
        st.markdown("### 🛡️ HITL Governance")
        st.caption("Human oversight of AI decisions")
        page = st.radio("Navigate", PAGES, key="page", label_visibility="collapsed")

        st.divider()
        st.text_input(
            "Reviewer name or ID",
            key="reviewer",
            placeholder="e.g. j.smith",
            help="Recorded in the audit log with every action you take.",
        )
        st.caption(f"Pending reviews: **{len(list_pending())}**")

        with st.expander("Demo tools"):
            st.caption("Deletes all decisions and the audit log, then reloads the sample data.")
            confirm = st.checkbox("I understand this erases the audit log")
            if st.button("Reset demo data", disabled=not confirm):
                db.reset_database()
                db.seed_from_json()
                for key in ("selected_decision", "review_started"):
                    st.session_state.pop(key, None)
                flash("success", "Demo data reset.")
                go_to("Decision Inbox")
                st.rerun()
    return page


# --------------------------------------------------------------------------- #
# Decision Inbox
# --------------------------------------------------------------------------- #
def render_confidence(decision: AIDecision) -> None:
    if decision.confidence is None:
        st.caption("AI confidence: not available")
        return
    note = " (low)" if decision.is_low_confidence else ""
    st.progress(decision.confidence, text=f"AI confidence: {decision.confidence:.0%}{note}")


def render_inbox() -> None:
    st.title("Decision Inbox")
    st.caption("AI outputs waiting for human review, most urgent first.")
    show_flash()

    pending = list_pending()
    if not pending:
        st.success("Nothing is waiting for review. New AI decisions will appear here.")
        return

    now = utc_now()
    waiting = max((now - d.created_at).total_seconds() for d in pending)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Pending", len(pending))
    c2.metric("High or critical risk", sum(d.risk_level.rank >= 2 for d in pending))
    c3.metric("Low confidence", sum(d.is_low_confidence for d in pending))
    c4.metric("Longest wait", format_duration(waiting, short=True))

    f1, f2, f3 = st.columns([2, 2, 3])
    risks = f1.multiselect("Risk level", ["critical", "high", "medium", "low"])
    categories = sorted({d.category for d in pending})
    chosen_cats = f2.multiselect("Category", categories)
    query = f3.text_input("Search", placeholder="Title, ID or AI output")

    def keep(d: AIDecision) -> bool:
        if risks and d.risk_level.value not in risks:
            return False
        if chosen_cats and d.category not in chosen_cats:
            return False
        if query and query.lower() not in f"{d.id} {d.title} {d.ai_output}".lower():
            return False
        return True

    visible = [d for d in pending if keep(d)]
    st.caption(f"Showing {len(visible)} of {len(pending)}")
    if not visible:
        st.info("No decisions match these filters. Clear a filter to see more.")

    for d in visible:
        with st.container(border=True):
            left, right = st.columns([6, 1], vertical_alignment="center")
            with left:
                st.markdown(
                    f"**{escape(d.title)}** &nbsp; {risk_badge_html(d.risk_level.value)}",
                    unsafe_allow_html=True,
                )
                st.caption(f"{d.id}  |  {d.category}  |  submitted {time_ago(d.created_at)}")
                st.write(truncate(d.ai_output, 220))
                render_confidence(d)
            with right:
                st.button("Review", key=f"open_{d.id}", on_click=open_review, args=(d.id,),
                          type="primary")


# --------------------------------------------------------------------------- #
# Review
# --------------------------------------------------------------------------- #
def render_context(context: dict) -> None:
    if not context:
        st.caption("No context provided.")
        return
    rows = [
        {"Field": k.replace("_", " ").capitalize(),
         "Value": ", ".join(map(str, v)) if isinstance(v, list) else str(v)}
        for k, v in context.items()
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True)


def render_review() -> None:
    st.title("Review Decision")
    show_flash()

    pending = list_pending()
    if not pending:
        st.success("Nothing is waiting for review.")
        st.button("Back to inbox", on_click=go_to, args=("Decision Inbox",))
        return

    ids = [d.id for d in pending]
    labels = {d.id: f"{d.id}: {d.title} ({d.risk_level.value})" for d in pending}
    selected = st.session_state.get("selected_decision")
    index = ids.index(selected) if selected in ids else 0
    decision_id = st.selectbox("Decision", ids, index=index, format_func=labels.get)
    st.session_state["selected_decision"] = decision_id

    decision = get_decision(decision_id)
    if decision is None:
        st.error("This decision no longer exists.")
        return

    # Start the review clock the first time this decision is opened in this session.
    started = st.session_state.setdefault("review_started", {})
    started.setdefault(decision_id, time.time())

    st.divider()
    main_col, side_col = st.columns([2, 1])
    with main_col:
        st.subheader(decision.title)
        st.markdown("**AI recommendation**")
        st.info(decision.ai_output)
        st.markdown("**Context**")
        render_context(decision.context)
    with side_col:
        st.markdown(risk_badge_html(decision.risk_level.value), unsafe_allow_html=True)
        st.write("")
        render_confidence(decision)
        if decision.is_low_confidence:
            st.warning("Low confidence. Check the context closely before approving.")
        st.caption(f"ID: {decision.id}")
        st.caption(f"Category: {decision.category}")
        st.caption(f"Submitted: {format_timestamp(decision.created_at)}")

    st.divider()
    st.subheader("Your decision")
    label = st.radio("Action", list(ACTION_LABELS), horizontal=True, key=f"action_{decision_id}")
    action = ACTION_LABELS[label]
    st.caption(ACTION_HELP[action])

    reason_key, final_key = f"reason_{decision_id}", f"final_{decision_id}"
    st.text_area(
        "Reason / comment",
        key=reason_key,
        placeholder=("Required: explain why." if action.requires_reason
                     else "Optional: add a note for the audit log."),
        height=100,
    )
    if action is ReviewAction.OVERRIDE:
        st.session_state.setdefault(final_key, decision.ai_output)
        st.text_area("Final decision (edit the AI output or write your own)", key=final_key,
                     height=100)

    reviewer = st.session_state.get("reviewer", "").strip()
    if not reviewer:
        st.warning("Enter your reviewer name in the sidebar to submit.")

    if st.button("Submit decision", type="primary", disabled=not reviewer):
        try:
            submission = ReviewSubmission(
                decision_id=decision_id,
                reviewer=reviewer,
                action=action,
                reason=st.session_state.get(reason_key, ""),
                final_decision=(st.session_state.get(final_key)
                                if action is ReviewAction.OVERRIDE else None),
            )
            elapsed = time.time() - started[decision_id]
            entry = submit_review(submission, review_seconds=elapsed)
        except ValidationError as exc:
            st.error(format_validation_error(exc))
        except ReviewError as exc:
            st.error(str(exc))
        else:
            started.pop(decision_id, None)
            st.session_state.pop("selected_decision", None)
            flash("success", f"{entry.decision_id} {entry.action.resulting_status.value} and "
                             f"recorded in the audit log (entry #{entry.id}).")
            go_to("Decision Inbox")
            st.rerun()


# --------------------------------------------------------------------------- #
# Audit Log
# --------------------------------------------------------------------------- #
AUDIT_COLUMNS = {
    "id": "Entry",
    "decided_at": "Decided at (UTC)",
    "decision_id": "Decision ID",
    "decision_title": "Title",
    "category": "Category",
    "risk_level": "Risk",
    "confidence": "AI confidence",
    "ai_output": "AI output",
    "action": "Action",
    "reviewer": "Reviewer",
    "reason": "Reason",
    "final_decision": "Final decision",
    "review_seconds": "Review time (s)",
}


def filter_audit(audit: pd.DataFrame) -> pd.DataFrame:
    audit = audit.copy()
    audit["_date"] = pd.to_datetime(audit["decided_at"], utc=True, format="ISO8601").dt.date

    c1, c2, c3 = st.columns(3)
    actions = c1.multiselect("Action", sorted(audit["action"].unique()))
    reviewers = c2.multiselect("Reviewer", sorted(audit["reviewer"].unique()))
    risks = c3.multiselect("Risk level", [r for r in ["critical", "high", "medium", "low"]
                                          if r in set(audit["risk_level"])])
    d1, d2 = st.columns([3, 2])
    query = d1.text_input("Search", placeholder="Reviewer, decision, reason, AI output...")
    lo, hi = audit["_date"].min(), audit["_date"].max()
    picked = d2.date_input("Date range", value=(lo, hi), min_value=lo, max_value=hi)

    mask = pd.Series(True, index=audit.index)
    if actions:
        mask &= audit["action"].isin(actions)
    if reviewers:
        mask &= audit["reviewer"].isin(reviewers)
    if risks:
        mask &= audit["risk_level"].isin(risks)
    if isinstance(picked, (tuple, list)) and len(picked) == 2:
        mask &= audit["_date"].between(picked[0], picked[1])
    if query:
        text_cols = ["decision_id", "decision_title", "category", "ai_output",
                     "reviewer", "reason", "final_decision"]
        haystack = audit[text_cols].fillna("").astype(str).agg(" ".join, axis=1)
        mask &= haystack.str.contains(query, case=False, regex=False)
    return audit[mask].drop(columns="_date")


def render_audit_log() -> None:
    st.title("Audit Log")
    st.caption("Every human action on an AI decision. Entries cannot be edited or deleted.")
    _, audit = load_frames()
    if audit.empty:
        st.info("No reviews yet. Review a decision from the inbox and it will appear here.")
        return

    filtered = filter_audit(audit)
    view = filtered[list(AUDIT_COLUMNS)].rename(columns=AUDIT_COLUMNS)
    st.caption(f"{len(view)} of {len(audit)} entries")
    st.dataframe(
        view,
        hide_index=True,
        column_config={
            "AI confidence": st.column_config.NumberColumn(format="percent"),
            "Review time (s)": st.column_config.NumberColumn(format="%.1f"),
        },
    )

    stamp = utc_now().strftime("%Y%m%d_%H%M%S")
    b1, b2, b3, _ = st.columns([1, 1, 1.5, 3])
    b1.download_button("Download CSV", dataframe_to_csv_bytes(view),
                       f"audit_log_{stamp}.csv", "text/csv")
    b2.download_button("Download JSON", dataframe_to_json_bytes(view),
                       f"audit_log_{stamp}.json", "application/json")
    if b3.button("Save CSV to reports/"):
        REPORTS_DIR.mkdir(exist_ok=True)
        path = REPORTS_DIR / f"audit_log_{stamp}.csv"
        path.write_bytes(dataframe_to_csv_bytes(view))
        st.success(f"Saved {path.relative_to(db.PROJECT_ROOT)}")


# --------------------------------------------------------------------------- #
# Governance Metrics
# --------------------------------------------------------------------------- #
def render_metrics() -> None:
    st.title("Governance Metrics")
    decisions, audit = load_frames()
    m = metrics_service.compute_metrics(decisions, audit)

    r1 = st.columns(4)
    r1[0].metric("Decisions reviewed", m.reviewed)
    r1[1].metric("Pending", m.pending, help="Waiting for a human reviewer")
    r1[2].metric("High-risk pending", m.high_risk_pending)
    r1[3].metric("Oldest pending", format_duration(m.oldest_pending_seconds, short=True))

    r2 = st.columns(4)
    r2[0].metric("Approval rate", format_percent(m.approval_rate))
    r2[1].metric("Rejection rate", format_percent(m.rejection_rate))
    r2[2].metric("Override rate", format_percent(m.override_rate),
                 help="How often humans replaced the AI output with their own decision.")
    r2[3].metric("Avg review time", format_duration(m.avg_review_seconds),
                 help="Time a reviewer spent on the review screen before submitting.")

    if audit.empty:
        st.info("Charts appear after the first review.")
        return
    st.caption(f"Average time from submission to decision: "
               f"{format_duration(m.avg_turnaround_seconds)}")

    by_risk = metrics_service.outcomes_by_risk(audit)
    totals = by_risk.groupby("action", as_index=False)["count"].sum()
    try:
        import plotly.express as px
    except ImportError:  # Plotly is optional
        st.bar_chart(totals.set_index("action")["count"])
        st.bar_chart(by_risk.pivot(index="risk_level", columns="action", values="count"))
        return

    left, right = st.columns(2)
    pie = px.pie(totals, names="action", values="count", hole=0.5, title="Review outcomes",
                 color="action", color_discrete_map=OUTCOME_COLORS)
    pie.update_layout(margin=dict(t=50, b=10, l=10, r=10))
    left.plotly_chart(pie)

    bar = px.bar(by_risk, x="risk_level", y="count", color="action", barmode="group",
                 title="Outcomes by risk level", color_discrete_map=OUTCOME_COLORS,
                 category_orders={"risk_level": metrics_service.RISK_ORDER},
                 labels={"risk_level": "Risk level", "count": "Decisions"})
    bar.update_layout(margin=dict(t=50, b=10, l=10, r=10))
    right.plotly_chart(bar)


# --------------------------------------------------------------------------- #
def main() -> None:
    bootstrap()
    page = render_sidebar()
    {
        "Decision Inbox": render_inbox,
        "Review": render_review,
        "Audit Log": render_audit_log,
        "Governance Metrics": render_metrics,
    }[page]()


main()
