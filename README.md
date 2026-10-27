# Human-in-the-Loop Governance Dashboard
 
A practical tool for **human oversight of AI decisions**. High-risk AI outputs land in a review inbox, where a person can **approve, reject, or override** them. Rejections and overrides require a written reason. Every action is written to a tamper-resistant audit log, and simple governance metrics show how oversight is working in practice.
 
Built as a portfolio project to demonstrate **accountability, traceability, and meaningful human control** in AI governance.
 
## Why I built this
 
Many AI governance frameworks say that high-risk AI decisions need "human oversight", but it is often unclear what that looks like in practice. A person clicking "OK" on every recommendation is not oversight. It is a rubber stamp.
 
I built this dashboard to turn the principle into something concrete and testable. It answers four practical questions:
 
- **Who** reviewed each AI decision, and **when**?
- **What** did they decide: accept, reject, or replace the AI's output?
- **Why** did they decide that, in their own words?
- **Is the oversight working?** Are humans actually challenging the AI, or approving everything within seconds?
The goal is to show that human oversight can be designed into a workflow, recorded, and measured, rather than just stated in a policy document.
 
## Governance principles demonstrated
 
| Principle | How the dashboard puts it into practice |
|---|---|
| **Accountability through mandatory reasons** | A reject or override cannot be submitted without a written reason (at least 10 characters), and an override must include the replacement decision. Reviewers have to explain themselves, and the explanation is stored with their name. |
| **Traceability through a tamper-resistant audit log** | Every review writes one audit entry: reviewer, timestamp, action, reason, final decision and a snapshot of the original AI output. Database triggers block any edit or deletion of these entries, so the history of who decided what cannot be quietly rewritten through the app. |
| **Risk-proportionate oversight through a risk-first queue** | Pending decisions are ordered by risk level, then by age, so critical and high-risk items are reviewed first. Low-confidence AI outputs are flagged to prompt closer scrutiny before approval. |
| **Measurable oversight** | Approval, rejection and override rates, average review time and the high-risk backlog show whether humans are exercising real judgment. A high override rate suggests the model is not trustworthy for that use case. A near-zero rate combined with very short review times may suggest rubber-stamping. |
| **Data integrity** | The status change and the audit entry are saved together or not at all, and a decision can only be reviewed once, even if two reviewers submit at the same time. |
 
## Features
 
| Area | What it does |
|---|---|
| **Decision Inbox** | Pending AI decisions ordered by risk, then age. Shows AI output, confidence, risk level and context. Filter by risk, category or text. |
| **Review** | Full context for one decision. Approve, Reject, or Override (edit the final decision). Reason is **mandatory** for reject and override. |
| **Audit Log** | Who did what, when, to which decision, and why. Filter by action, reviewer, risk, date; free-text search; export to **CSV or JSON**. |
| **Governance Metrics** | Reviewed and pending counts, approval / rejection / override rates, average review time, time-to-decision, high-risk backlog, outcome charts. |
 
## Quick start
 
Requires Python 3.11+.
 
```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
 
streamlit run app/main.py
```
 
The database (`data/governance.db`) is created on first launch and seeded with 13 sample decisions from `data/sample_decisions.json`. Set `HITL_DB_PATH` to use a different location.
 
### Example walkthrough
 
1. Enter your name in the sidebar (it is recorded in the audit log).
2. Open **DEC-1003 (emergency triage, critical)** from the inbox.
3. Choose **Override**, write a reason, and edit the final decision to "Assign triage priority Level 2".
4. Open **Audit Log** to see the entry, then download it as CSV or JSON.
5. Open **Governance Metrics** to see the override rate update.
### Tests
 
```bash
pip install -r requirements-dev.txt
pytest
```
 
Covers validation rules, the review workflow, rollback on failure, audit-log immutability, metrics, and an end-to-end UI test using Streamlit's `AppTest`.
 
## Using your own AI decisions
 
Add items to `data/sample_decisions.json` (or point `seed_from_json()` at another file):
 
```json
{
  "id": "DEC-2001",
  "title": "Short description",
  "category": "Credit",
  "ai_output": "What the AI recommended",
  "confidence": 0.72,
  "risk_level": "high",
  "context": {"any": "key-value details a reviewer needs"},
  "submitted_hours_ago": 4
}
```
 
`confidence` (0 to 1) may be `null`. `risk_level` is `low`, `medium`, `high` or `critical`. Existing IDs are skipped, so loading is safe to repeat.
 
## Project structure
 
```
human-in-the-loop-dashboard/
├── app/
│   ├── main.py              # Streamlit UI (inbox, review, audit log, metrics)
│   ├── database.py          # SQLite schema, triggers, data access
│   ├── models.py            # Pydantic models, enums, validation rules
│   ├── utils.py             # Formatting and export helpers
│   └── services/
│       ├── review.py        # Approve / Reject / Override workflow
│       └── metrics.py       # Governance metrics (pure pandas functions)
├── data/sample_decisions.json
├── reports/                 # Saved audit-log exports
├── tests/
├── requirements.txt
└── README.md
```
 
## Data model
- **`decisions`**: `id`, `title`, `category`, `ai_output`, `confidence`, `risk_level`, `context` (JSON), `status`, `final_decision`, `created_at`, `reviewed_at`
- **`audit_log`** (append-only): `id`, `decision_id`, `decision_title`, `category`, `risk_level`, `confidence`, `ai_output`, `reviewer`, `action`, `reason`, `final_decision`, `review_seconds`, `decided_at`
## Known limitations
- This is an MVP intended for demonstration, not production use. Being clear about what it does not do is part of good governance.
- **No authentication.** The reviewer name is self-declared, so the audit log records who a person *says* they are. Production use needs SSO and role-based access.
- **Tamper resistance is database-level only.** The triggers stop edits and deletes through the app, but a user with direct access to the SQLite file could still modify it. Hash-chaining entries or writing to external, write-once storage would strengthen this.
- **Single review per decision.** There is no second-reviewer (four-eyes) workflow for critical items.
- **Reasons are checked for presence, not quality.** The 10-character minimum stops empty answers, but it cannot tell a thoughtful reason from a filler one.
- **Sample data only.** There is no live integration with a real model, so decisions are loaded from a JSON file.

## Roadmap ideas
- Review SLAs and escalation for items that wait too long
- A second-reviewer requirement for critical decisions
- Hash-chained audit entries to detect tampering
- Reviewer-disagreement tracking and rubber-stamping indicators
- An API for ingesting decisions from real models
Timestamps are stored and displayed in UTC.
 
## Contributing
- Contributions are welcome! You can help improve the tool by reporting bugs, suggesting features, refining governance controls, or adding example assessments.
- Report bugs or suggest improvements
- Open a GitHub issue describing:
- The problem or proposed improvement
- Steps to reproduce the issue, if applicable
- Expected and actual behaviour
- Screenshots or fictional example inputs, where helpful
- Do not include personal data, confidential system descriptions, or credentials.
- Submit a pull request
1. Fork the repository.
- Create a branch for your change:
- `git checkout -b feature/your-improvement`
2. Make your changes using clear code and type hints.
3. Update tests and documentation when your change affects behaviour.
4. Open a pull request explaining what changed, why, and how you tested it.


## License
- This project is licensed under the MIT License.
- MIT License
```
Copyright (c) 2026 Melisa Sever

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```