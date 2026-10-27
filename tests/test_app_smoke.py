"""End-to-end UI smoke test using Streamlit's AppTest harness."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

MAIN = str(Path(__file__).resolve().parent.parent / "app" / "main.py")


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setattr("app.database.DEFAULT_DB_PATH", tmp_path / "ui.db")
    at = AppTest.from_file(MAIN, default_timeout=30)
    at.run()
    assert not at.exception
    return at


def test_inbox_lists_pending_decisions(app):
    assert app.title[0].value == "Decision Inbox"
    assert len([b for b in app.button if b.label == "Review"]) == 13


def test_full_review_flow(app):
    app.text_input(key="reviewer").set_value("j.smith").run()
    # open the first (most urgent) item
    next(b for b in app.button if b.label == "Review").click().run()
    assert app.title[0].value == "Review Decision"

    # Reject with no reason -> blocked
    next(r for r in app.radio if r.label == "Action").set_value("Reject").run()
    next(b for b in app.button if b.label == "Submit decision").click().run()
    assert any("reason" in e.value.lower() for e in app.error)

    # Reject with a reason -> succeeds and returns to inbox
    app.text_area[0].set_value("Symptoms warrant a higher priority level").run()
    next(b for b in app.button if b.label == "Submit decision").click().run()
    assert not app.exception
    assert app.title[0].value == "Decision Inbox"
    assert any("audit log" in s.value for s in app.success)

    # Audit log and metrics render
    app.radio(key="page").set_value("Audit Log").run()
    assert not app.exception and len(app.dataframe) == 1
    app.radio(key="page").set_value("Governance Metrics").run()
    assert not app.exception
