"""Small, dependency-light helpers shared across the app."""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
from pydantic import ValidationError

RISK_COLORS: dict[str, tuple[str, str]] = {
    # risk level -> (text colour, background colour)
    "low": ("#1B5E20", "#E3F1E4"),
    "medium": ("#7A5600", "#FFF1CC"),
    "high": ("#9A3412", "#FDE4D3"),
    "critical": ("#8E1B1B", "#F9D9D9"),
}


def utc_now() -> datetime:
    """Timezone-aware current time in UTC."""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def format_timestamp(value: datetime | str | None) -> str:
    """Render a timestamp as ``YYYY-MM-DD HH:MM UTC``."""
    if value is None or value == "":
        return "-"
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def time_ago(value: datetime, now: Optional[datetime] = None) -> str:
    """Human-friendly age such as ``3h ago`` or ``2d ago``."""
    seconds = max(0.0, ((now or utc_now()) - value).total_seconds())
    return f"{format_duration(seconds, short=True)} ago"


def format_duration(seconds: Optional[float], short: bool = False) -> str:
    """Format seconds as ``1m 23s`` (or ``2h`` / ``3d`` when ``short``)."""
    if seconds is None or pd.isna(seconds):
        return "-"
    seconds = float(seconds)
    if short:
        if seconds < 60:
            return "<1m"
        if seconds < 3600:
            return f"{int(seconds // 60)}m"
        if seconds < 86400:
            return f"{int(seconds // 3600)}h"
        return f"{int(seconds // 86400)}d"
    minutes, secs = divmod(int(round(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def format_percent(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:.0%}"


def truncate(text: str, limit: int = 200) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def risk_badge_html(risk: str) -> str:
    """Small coloured pill for a risk level (value is escaped)."""
    fg, bg = RISK_COLORS.get(risk, ("#333", "#EEE"))
    label = html.escape(risk.capitalize())
    return (
        f'<span style="background:{bg};color:{fg};padding:2px 10px;border-radius:999px;'
        f'font-size:0.8rem;font-weight:600;">{label} risk</span>'
    )


def dataframe_to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


def dataframe_to_json_bytes(df: pd.DataFrame) -> bytes:
    records = json.loads(df.to_json(orient="records", date_format="iso"))
    return json.dumps(records, indent=2, ensure_ascii=False).encode("utf-8")


def format_validation_error(exc: ValidationError) -> str:
    """Turn a Pydantic error into a single, user-readable sentence."""
    messages = []
    for err in exc.errors():
        msg = err.get("msg", "Invalid input")
        messages.append(msg.removeprefix("Value error, "))
    return " ".join(messages)
