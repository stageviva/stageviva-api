"""Stable card data and filters derived from structured opportunities."""

from __future__ import annotations

from typing import Any


def field_value(document: dict[str, Any], *path: str) -> str:
    value: Any = document
    for key in path:
        if not isinstance(value, dict):
            return ""
        value = value.get(key)
    if isinstance(value, dict):
        value = value.get("value", "")
    return str(value or "").strip()


def _known(value: str) -> bool:
    normalized = value.strip().lower()
    return bool(normalized) and not (
        normalized in {"unknown", "not specified", "n/a"}
        or normalized.startswith("unknown;")
        or normalized.startswith("unknown ")
    )


def _public_url(*candidates: str) -> str:
    """Return the first usable public link, never an extraction placeholder."""
    for candidate in candidates:
        value = str(candidate or "").strip()
        if not _known(value):
            continue
        if value.startswith(("https://", "http://")):
            return value
        if value.startswith("www."):
            return f"https://{value}"
    return ""


def opportunity_categories(opportunity: dict[str, Any]) -> list[str]:
    text = " ".join((
        field_value(opportunity, "identity", "title"),
        field_value(opportunity, "identity", "opportunity_type"),
        field_value(opportunity, "identity", "description"),
        field_value(opportunity, "contract_and_compensation", "contract_type"),
    )).lower()
    categories: list[str] = []
    rules = {
        "ballet": ("ballet",),
        "contemporary": ("contemporary",),
        "musical_theatre": ("musical", "broadway", "singer-actor", "dancer-singer"),
        "cruise": ("cruise", "at sea", "onboard"),
        "acting": ("actor", "acting", "screen", "series", "film", "extras"),
        "singing": ("singer", "vocalist", "vocal"),
        "modelling": ("model", "modelling", "modeling"),
        "commercial": ("commercial", "advert", "campaign"),
        "circus_acro": ("acrobat", "aerial", "circus"),
        "agency": ("agency", "agent", "representation", "talent management"),
    }
    for category, markers in rules.items():
        if any(marker in text for marker in markers):
            categories.append(category)
    return categories or ["performing_arts"]


def opportunity_track(opportunity: dict[str, Any]) -> str:
    text = " ".join((
        field_value(opportunity, "identity", "opportunity_type"),
        field_value(opportunity, "identity", "description"),
        field_value(opportunity, "contract_and_compensation", "contract_type"),
    )).lower()
    if any(marker in text for marker in ("apprentice", "apprenticeship", "trainee", "young artist", "young-artist", "pre-professional")):
        return "apprenticeship"
    return "contract"


def opportunity_card(match_item: dict[str, Any]) -> dict[str, Any]:
    opportunity = match_item["opportunity"]
    organisation = field_value(opportunity, "identity", "organisation")
    listing_title = field_value(opportunity, "identity", "title")
    description = field_value(opportunity, "identity", "description")
    role_summary = field_value(opportunity, "identity", "opportunity_type")
    # A source sometimes reports an internal "unknown; members only" company
    # value. In that case, the listing's own title is still meaningful to a
    # performer and must not be thrown away for a generic placeholder.
    title = (
        organisation if _known(organisation)
        else listing_title if _known(listing_title)
        else description if _known(description)
        else "Opportunity details being verified"
    )
    deadline = field_value(opportunity, "dates", "application_deadline")
    city = field_value(opportunity, "location", "city")
    country = field_value(opportunity, "location", "country")
    location = ", ".join(part for part in (city, country) if _known(part)) or "Unknown location"
    overall = match_item["match"].get("overall", {})
    return {
        "title": title,
        "role_summary": role_summary if _known(role_summary) else "Performance opportunity",
        "deadline": "ASAP" if deadline.lower() in {"as soon as possible", "asap"} or not _known(deadline) else deadline,
        "location": location,
        "match_score": overall.get("match_score", 0),
        "match_level": overall.get("match_level", "unknown"),
        "track": opportunity_track(opportunity),
        "categories": opportunity_categories(opportunity),
    }


def _known_list(document: dict[str, Any], *path: str) -> list[str]:
    value: Any = document
    for key in path:
        if not isinstance(value, dict):
            return []
        value = value.get(key)
    if isinstance(value, dict):
        value = value.get("value", [])
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if _known(str(item).strip())]


def opportunity_detail(match_item: dict[str, Any]) -> dict[str, Any]:
    """A performer-facing detail view; never leak the extraction schema."""
    opportunity = match_item["opportunity"]
    card = opportunity_card(match_item)
    match = match_item.get("match", {})
    requirements = opportunity.get("requirements", {})
    application = opportunity.get("application", {})
    source = opportunity.get("source", {})
    application_url = _public_url(
        field_value(application, "application_url"),
        field_value(source, "official_url"),
        match_item["listing_url"],
    )
    return {
        "opportunity_id": match_item["opportunity_id"],
        "company": card["title"],
        "role": card["role_summary"],
        "description": field_value(opportunity, "identity", "description") or "Details are being verified.",
        "location": card["location"],
        "deadline": card["deadline"],
        "audition_dates": _known_list(opportunity, "dates", "audition_dates"),
        "contract_start": field_value(opportunity, "dates", "contract_start") or "Unknown",
        "contract_end": field_value(opportunity, "dates", "contract_end") or "Unknown",
        "contract_type": field_value(opportunity, "contract_and_compensation", "contract_type") or "Unknown",
        "compensation": field_value(opportunity, "contract_and_compensation", "compensation") or "Not specified",
        "requirements": {
            "styles": _known_list(requirements, "styles"),
            "techniques": _known_list(requirements, "techniques"),
            "experience": field_value(requirements, "experience", "required_level") or "Not specified",
            "work_rights": field_value(requirements, "work_rights_or_visa") or "Not specified",
        },
        "application": {
            "method": field_value(application, "method") or "See the official listing",
            "url": application_url,
            "email": field_value(application, "email"),
            "materials": _known_list(application, "materials"),
        },
        "match": {
            "score": card["match_score"],
            "level": card["match_level"],
            "summary": match.get("overall", {}).get("summary", "Match details are being prepared."),
            "reasons": [item.get("reason", "") for item in match.get("match_reasons", []) if item.get("reason")],
            "gaps": [item.get("description", "") for item in match.get("gaps", []) if item.get("description")],
            "missing_information": match.get("missing_information", []),
        },
    }


def matches_for_filters(
    matches: list[dict[str, Any]], *, track: str | None, categories: set[str],
) -> list[dict[str, Any]]:
    result = []
    for item in matches:
        card = opportunity_card(item)
        if track and card["track"] != track:
            continue
        if categories and not categories.intersection(card["categories"]):
            continue
        result.append({**item, "card": card})
    return result
