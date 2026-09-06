"""Rules for deciding whether a structured opportunity is still actionable."""

from __future__ import annotations

from datetime import date
from typing import Any

from dateutil import parser as date_parser


def _value(opportunity: dict[str, Any], *path: str) -> Any:
    current: Any = opportunity
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _parse_date(value: str) -> date | None:
    if not value or value.lower() == "unknown":
        return None
    try:
        return date_parser.parse(value, fuzzy=True, dayfirst=True).date()
    except (OverflowError, TypeError, ValueError):
        return None


def is_current_opportunity(opportunity: dict[str, Any], today: date | None = None) -> bool:
    """Return false only when every explicit application/audition date has passed."""
    today = today or date.today()
    candidates: list[str] = []
    deadline = _value(opportunity, "dates", "application_deadline", "value")
    if isinstance(deadline, str):
        candidates.append(deadline)
    audition_dates = _value(opportunity, "dates", "audition_dates", "value")
    if isinstance(audition_dates, list):
        candidates.extend(value for value in audition_dates if isinstance(value, str))
    parsed = [parsed_date for value in candidates if (parsed_date := _parse_date(value))]
    return not parsed or any(value >= today for value in parsed)
