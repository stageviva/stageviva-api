"""Rules for deciding whether a structured opportunity is still actionable."""

from __future__ import annotations

from datetime import date
import re
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
    """Return false for expired deadlines, auditions and past season notices."""
    today = today or date.today()
    candidates: list[str] = []
    deadline = _value(opportunity, "dates", "application_deadline", "value")
    if isinstance(deadline, str):
        candidates.append(deadline)
    audition_dates = _value(opportunity, "dates", "audition_dates", "value")
    if isinstance(audition_dates, list):
        candidates.extend(value for value in audition_dates if isinstance(value, str))
    parsed = [parsed_date for value in candidates if (parsed_date := _parse_date(value))]
    if parsed and not any(value >= today for value in parsed):
        return False

    # Sources frequently publish a title such as "2025/26 season" without a
    # separate end date. After the following August that season is no longer a
    # viable opportunity, even when its extracted deadline is unknown.
    searchable = " ".join(str(value or "") for value in (
        _value(opportunity, "identity", "title", "value"),
        _value(opportunity, "identity", "opportunity_type", "value"),
        _value(opportunity, "identity", "description", "value"),
        _value(opportunity, "contract_and_compensation", "contract_type", "value"),
    )).lower()
    for start, end in re.findall(r"\b(20\d{2})\s*(?:-|–|—|/)\s*(\d{2}|20\d{2})\s*season\b", searchable):
        end_year = int(end) if len(end) == 4 else int(start[:2] + end)
        if end_year < today.year or (end_year == today.year and today.month >= 8):
            return False
    return True
