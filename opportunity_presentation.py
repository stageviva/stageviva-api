"""Stable card data and filters derived from structured opportunities."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse
from opportunity_quality import PATHWAY_LABELS, application_pathway, safe_application_url
from opportunity_source_facts import complete_source_facts


_DIRECTORY_DOMAINS = frozenset({
    "balletplaces.com", "danceeurope.net", "ballee.co", "entertainersworldwidejobs.com", "allcasting.com",
})
OPPORTUNITY_TRACKS = frozenset({"contract", "agency", "apprenticeship"})
OPPORTUNITY_CATEGORIES = frozenset({
    "ballet", "contemporary", "jazz", "commercial", "hip_hop",
    "musical_theatre", "tap", "cruise", "acting", "singing", "modelling",
    "circus_acro", "agency", "performing_arts",
})


def _admin_overrides(opportunity: dict[str, Any]) -> dict[str, Any]:
    value = opportunity.get("stageviva_admin", {})
    return value if isinstance(value, dict) else {}


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
        normalized in {"unknown", "not specified", "n/a", "none"}
        or normalized.startswith("unknown;")
        or normalized.startswith("unknown ")
    )


_CURRENCY = re.compile(r"(?:[$€£¥]\s*\d|\b(?:USD|EUR|GBP|CAD|AUD)\s*\d|\b\d[\d,.]*\s*(?:USD|EUR|GBP|CAD|AUD)\b)", re.I)
_TITLE_METADATA = re.compile(
    r"\s*(?:[|–—]|\s-\s)?\s*\b(?:application deadline|submission deadline|audition dates?|auditions|"
    r"salary|pay rate|compensation|dates to be arranged)\s*(?::|$)", re.I,
)
_TITLE_SCHEDULE = re.compile(
    r"\s+[-–—]\s+(?:20\d{2}\s*[-–/]\s*20\d{2}\s+season|[a-z]+\s+20\d{2}\s+start)!?$", re.I,
)
_TITLE_WORD = re.compile(r"[^\W\d_]+(?:[’'][^\W\d_]+)?")
_ACRONYMS = {"ABT", "BBC", "CV", "EU", "GCSE", "ISH", "NYC", "UK", "US", "USA", "UAE"}
_THEATRE_NAMES = ("Broadway", "West End")


def _role_title(
    title: str, organisation: str, role_summary: str, compensation: str = "", *,
    preserve_case: bool = False, proper_names: tuple[str, ...] = (),
) -> str:
    title = " ".join(title.split()).strip(" -–—|")
    title = _TITLE_METADATA.split(title, maxsplit=1)[0]
    title = _TITLE_SCHEDULE.sub("", title)
    if salary := _CURRENCY.search(title):
        title = title[:salary.start()]
    elif amount := re.search(r"\s+(\d[\d,.]*)$", title):
        normalized_amount = re.sub(r"[,.]", "", amount[1])
        known_amounts = {re.sub(r"[,.]", "", value) for value in re.findall(r"\d[\d,.]*", compensation)}
        if normalized_amount in known_amounts:
            title = title[:amount.start()]
    title = title.strip(" (:-–—|")
    title = title if _known(title) else role_summary if _known(role_summary) else ""
    title = re.sub(r"^(?:casting call|audition)\s*(?:[:–—-]\s*|for\s+(?:an?\s+)?)", "", title, flags=re.I)
    title = re.sub(r"^seeking\s+|\s+(?:wanted|required|needed)$", "", title, flags=re.I)
    title = re.sub(r"\b(?:wanted|required|needed)\s+for\b", "for", title, flags=re.I)
    title = re.sub(r"\s*&\s+(?=\S)", " and ", title)
    title = re.sub(r"\s+[-–—|]\s+", " — ", title)
    title = re.sub(r"(?<=\w)-\s+(?=\w)", "-", title)

    def title_word(match: re.Match[str]) -> str:
        word = match[0]
        if word.isupper() and len(word) == 2 and title[:match.start()].rstrip().endswith(","):
            return word
        if word in _ACRONYMS or (not word.isupper() and not word.islower() and not word.istitle()):
            return word
        return word.lower()

    # Legacy headings can contain the only surviving job context. Keep their
    # descriptive wording while normalising case, rather than reducing them to
    # a generic role. New extractions are written semantically by the model.
    formatted = title.strip() if preserve_case else _TITLE_WORD.sub(title_word, title.strip())
    for name in (organisation, *proper_names):
        if not _known(name):
            continue
        proper_name = name
        if name.isupper():
            proper_name = _TITLE_WORD.sub(
                lambda match: match[0] if match[0] in _ACRONYMS else match[0].capitalize(),
                name,
            )
        formatted = re.sub(rf"(?<!\w){re.escape(name)}(?!\w)", lambda _: proper_name, formatted, flags=re.I)
    for proper_name in _THEATRE_NAMES:
        formatted = re.sub(rf"\b{re.escape(proper_name)}\b", lambda _: proper_name, formatted, flags=re.I)
    for quoted in re.findall(r'["“]([^"”]+)["”]', title):
        proper_name = quoted if not quoted.isupper() else quoted.title()
        formatted = re.sub(re.escape(quoted), lambda _: proper_name, formatted, flags=re.I)
    return formatted[:1].upper() + formatted[1:]


_REQUIREMENT_PLACEHOLDERS = {"low", "medium", "high", "very_high", "audition", "age", "height", "gender", "nationality"}


def _requirement_points(values: list[str]) -> list[str]:
    points: dict[str, str] = {}
    for value in values:
        for line in re.split(r"\r?\n\s*(?=[-*•]|\d+[.)]\s)|•", value):
            line = " ".join(line.split()).strip(" -–—*·.;")
            line = re.sub(r"^\d+[.)]\s+", "", line)
            if not _known(line) or line.casefold() in _REQUIREMENT_PLACEHOLDERS:
                continue
            if line.isupper():
                line = _TITLE_WORD.sub(lambda match: match[0] if match[0] in _ACRONYMS else match[0].lower(), line)
            line = re.sub(r"\bminimum height\s*:\s*", "minimum height of ", line, flags=re.I)
            line = line[0].upper() + line[1:]
            points.setdefault(line.casefold(), line)
    return list(points.values())


def _physical_requirement_point(label: str, value: str) -> str:
    if label == "Age" and value[0].isdigit():
        return f"Age {value}"
    if label == "Height" and value[0].isdigit():
        return f"Height of {value}"
    if label == "Casting requirement" and value.casefold() in {"male", "female"}:
        return f"{value} dancers"
    return value


def _public_url(*candidates: str) -> str:
    """Return the first usable public link, never an extraction placeholder."""
    for candidate in candidates:
        value = str(candidate or "").strip()
        if not _known(value):
            continue
        if safe_application_url(value):
            return value
    return ""


def _is_directory_url(url: str) -> bool:
    host = urlparse(str(url or "")).hostname or ""
    host = host.lower().removeprefix("www.")
    return host in _DIRECTORY_DOMAINS


def opportunity_categories(opportunity: dict[str, Any]) -> list[str]:
    override = _admin_overrides(opportunity).get("categories")
    if isinstance(override, list):
        selected = [str(value) for value in override if str(value) in OPPORTUNITY_CATEGORIES]
        if selected:
            return list(dict.fromkeys(selected))
    requirements = opportunity.get("requirements", {})
    discipline_requirements = requirements.get("discipline_requirements", {}) if isinstance(requirements, dict) else {}
    styles = _known_list(requirements, "styles")
    demonstrated_disciplines = [
        str(name).replace("_", " ")
        for name, requirement in discipline_requirements.items()
        if isinstance(requirement, dict) and _known(str(requirement.get("required_level", "")))
    ] if isinstance(discipline_requirements, dict) else []
    text = " ".join((
        field_value(opportunity, "identity", "title"),
        field_value(opportunity, "identity", "opportunity_type"),
        field_value(opportunity, "identity", "description"),
        field_value(opportunity, "contract_and_compensation", "contract_type"),
        " ".join(styles),
        " ".join(demonstrated_disciplines),
    )).lower()
    categories: list[str] = []
    rules = {
        "ballet": ("ballet",),
        "contemporary": ("contemporary",),
        "jazz": ("jazz",),
        "commercial": ("commercial",),
        "hip_hop": ("hip hop", "hip-hop"),
        "musical_theatre": ("musical", "broadway", "singer-actor", "dancer-singer"),
        "tap": ("tap",),
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
    override = str(_admin_overrides(opportunity).get("track") or "")
    if override in OPPORTUNITY_TRACKS:
        return override
    text = " ".join((
        field_value(opportunity, "identity", "opportunity_type"),
        field_value(opportunity, "identity", "description"),
        field_value(opportunity, "contract_and_compensation", "contract_type"),
    )).lower()
    if any(marker in text for marker in ("agency", "agent", "representation", "talent management")):
        return "agency"
    if any(marker in text for marker in ("apprentice", "apprenticeship", "internship", "trainee", "young artist", "young-artist", "pre-professional")):
        return "apprenticeship"
    return "contract"


def opportunity_card(match_item: dict[str, Any]) -> dict[str, Any]:
    opportunity = complete_source_facts(match_item["opportunity"])
    organisation = field_value(opportunity, "identity", "organisation")
    listing_title = field_value(opportunity, "identity", "title")
    description = field_value(opportunity, "identity", "description")
    role_summary = field_value(opportunity, "identity", "opportunity_type")
    city = field_value(opportunity, "location", "city")
    country = field_value(opportunity, "location", "country")
    position = _role_title(
        listing_title, organisation, role_summary,
        field_value(opportunity, "contract_and_compensation", "compensation"),
        preserve_case=opportunity.get("stageviva_review", {}).get("editorial_style") == "sentence_case_with_context_v1",
        proper_names=(city, country),
    )
    # A source sometimes reports an internal "unknown; members only" company
    # value. In that case, the listing's own title is still meaningful to a
    # performer and must not be thrown away for a generic placeholder.
    title = (
        organisation if _known(organisation)
        else position if position
        else description if _known(description)
        else "Opportunity details being verified"
    )
    deadline = field_value(opportunity, "dates", "application_deadline")
    location = ", ".join(part for part in (city, country) if _known(part)) or "Unknown location"
    overall = match_item["match"].get("overall", {})
    assessment = match_item["match"].get("stageviva_assessment", {})
    return {
        "title": title,
        "position": position,
        "organisation": organisation if _known(organisation) else "",
        "role_summary": role_summary if _known(role_summary) else "Performance opportunity",
        "deadline": "ASAP" if deadline.lower() in {"as soon as possible", "asap"} else deadline if _known(deadline) else "",
        "location": location,
        "match_score": None if assessment.get("confidence") == "insufficient" else overall.get("match_score"),
        "match_level": overall.get("match_level", "unknown"),
        "assessment_confidence": assessment.get("confidence"),
        "eligibility_warning": assessment.get("warning"),
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


def opportunity_detail(match_item: dict[str, Any], *, legacy_application: bool = False) -> dict[str, Any]:
    """A performer-facing detail view; never leak the extraction schema."""
    opportunity = complete_source_facts(match_item["opportunity"])
    card = opportunity_card(match_item)
    match = match_item.get("match", {})
    requirements = opportunity.get("requirements", {})
    application = opportunity.get("application", {})
    pathway = application_pathway(opportunity)
    kind = pathway.get("kind")
    target = pathway.get("target", "")
    source_url = _public_url(match_item.get("listing_url", ""))
    if (legacy_application and "stageviva_review" not in opportunity
            and safe_application_url(source_url) and not _is_directory_url(source_url)):
        # Installed clients only read application.url. Keep their original
        # direct listing accessible without trusting a cross-site legacy link.
        target = source_url
    assessment = match.get("stageviva_assessment", {})
    physical = requirements.get("physical", {})
    candidate_requirements = [
        {"label": label, "value": value}
        for key, label in (("height_requirement", "Height"), ("age_requirement", "Age"), ("gender_requirement", "Casting requirement"))
        if _known(value := field_value(physical, key)) and value.casefold() not in {"age", "height", "gender"}
    ]
    experience = field_value(requirements, "experience", "required_level")
    if experience.casefold() in {"high", "medium", "low", "very_high", "audition"}:
        experience = ""
    experience_fields = requirements.get("experience") or {}
    experience_criteria = [
        field_value(item, "value") for item in (experience_fields.get("criteria") or [])
        if isinstance(item, dict)
    ]
    styles = _known_list(requirements, "styles")
    techniques = _known_list(requirements, "techniques")
    languages = _known_list(requirements, "languages")
    work_rights = field_value(requirements, "work_rights_or_visa")
    level_labels = {"very_high": "Exceptional", "high": "Advanced", "medium": "Intermediate", "low": "Basic"}
    discipline_points = []
    for name, requirement in (requirements.get("discipline_requirements") or {}).items():
        if not isinstance(requirement, dict) or not requirement.get("evidence"):
            continue
        label = level_labels.get(requirement.get("required_level"))
        if label:
            discipline = {"classical_ballet": "classical ballet"}.get(name, name.replace("_", " "))
            discipline_points.append(f"{label} {discipline} technique")
    summary = _requirement_points([
        *(_physical_requirement_point(item["label"], item["value"]) for item in candidate_requirements),
        *(_requirement_points(experience_criteria) or [experience]),
        *discipline_points, *styles, *techniques, *languages, work_rights,
        *_known_list(physical, "other_requirements"),
    ])
    return {
        "opportunity_id": match_item["opportunity_id"],
        "company": card["organisation"],
        "role": card["position"] or card["role_summary"],
        "track": card["track"],
        "categories": card["categories"],
        "description": field_value(opportunity, "identity", "description"),
        "listing_url": source_url,
        "location": card["location"],
        "deadline": card["deadline"],
        "audition_dates": _known_list(opportunity, "dates", "audition_dates"),
        "contract_start": field_value(opportunity, "dates", "contract_start") or "Unknown",
        "contract_end": field_value(opportunity, "dates", "contract_end") or "Unknown",
        "contract_type": field_value(opportunity, "contract_and_compensation", "contract_type") or "Unknown",
        "contract_duration": field_value(opportunity, "contract_and_compensation", "duration"),
        "compensation": field_value(opportunity, "contract_and_compensation", "compensation") or "Not specified",
        "requirements": {
            "summary": summary,
            "styles": styles,
            "techniques": techniques,
            "experience": experience,
            "work_rights": work_rights or "Not specified",
            "physical": candidate_requirements,
            "languages": languages,
        },
        "application": {
            "method": field_value(application, "method"),
            "url": target if kind != "email" else "",
            "email": target if kind == "email" else "",
            "label": PATHWAY_LABELS.get(kind, ""),
            "materials": _known_list(application, "materials"),
        },
        "match": {
            "score": card["match_score"],
            "level": card["match_level"],
            "confidence": assessment.get("confidence"),
            "status": assessment.get("eligibility"),
            "eligibility_warning": assessment.get("warning"),
            "checks": assessment.get("checks", []),
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
        # Preserve the existing response shape while keeping retained source
        # material and editorial notes on the admin side of the API.
        public_opportunity = {key: value for key, value in item["opportunity"].items() if key != "stageviva_review"}
        public_match = {key: value for key, value in item["match"].items() if key != "stageviva_assessment"}
        result.append({**item, "listing_url": _public_url(item.get("listing_url", "")), "match": public_match, "opportunity": public_opportunity, "card": card})
    return result
