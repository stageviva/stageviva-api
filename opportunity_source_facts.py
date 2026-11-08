"""Recover explicit labelled facts from retained evidence without paid analysis.

This is a narrow completeness backstop, not a substitute for extraction. It
never estimates technique from employer reputation or invents a missing year.
"""

import copy
import re
from datetime import datetime

from dateutil import parser

from opportunity_quality import value_at

MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
DATE = rf"(?:\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}(?:st|nd|rd|th)?\s+{MONTH}\s*,?\s*20\d{{2}}|{MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?\s*,?\s*20\d{{2}})"
DEADLINE = re.compile(rf"\b(?:application\s+deadline|deadline|apply\s+(?:by|before)|applications?\s+(?:close|closing))\b[^\w\d]{{0,30}}(?P<date>{DATE})", re.I)
LEVELS = {"excellent": "high", "advanced": "high", "strong": "high", "exceptional": "very_high", "outstanding": "very_high", "intermediate": "medium", "basic": "low"}
DISCIPLINES = {"classical": "classical_ballet", "classical ballet": "classical_ballet", "ballet": "classical_ballet", "contemporary": "contemporary", "jazz": "jazz", "improvisation": "improvisation", "tap": "tap", "partnering": "partnering", "acrobatics": "acrobatics"}
NAMES = r"(?:classical(?:\s+ballet)?|ballet|contemporary|jazz|improvisation|tap|partnering|acrobatics)\b"
GROUP = rf"{NAMES}(?:\s*(?:and|/|&|,)\s*{NAMES})*"
TECHNIQUE = re.compile(rf"\b(?P<level>{'|'.join(LEVELS)})\s+(?:(?:technique|skills?)\s*\(\s*(?P<bracket>{GROUP})\s*\)|(?P<plain>{GROUP})(?:\s+(?:technique|skills?))?)", re.I)


def known(value):
    return str(value or "").strip().casefold() not in {"", "unknown", "not specified", "n/a"}


def complete_source_facts(opportunity: dict, text: str | None = None) -> dict:
    """Return a copy; persisted records and editorial changes are never rewritten."""
    if text is None:
        sources = opportunity.get("stageviva_review", {}).get("sources", [])
        text = "\n\n".join(str(source.get("text", "")) for source in sources if isinstance(source, dict))
    if not text:
        return opportunity
    result = copy.deepcopy(opportunity)
    review = result.setdefault("stageviva_review", {})
    conflicts = list(review.get("conflicts") or [])
    dates = {}
    for match in DEADLINE.finditer(text):
        try:
            parsed = parser.parse(match["date"], default=datetime(2001, 1, 1)).date()
        except (ValueError, OverflowError):
            continue
        dates[parsed.isoformat()] = match.group()
    current = value_at(result, "dates", "application_deadline")
    if not known(current) and len(dates) == 1:
        value, quote = next(iter(dates.items()))
        result.setdefault("dates", {})["application_deadline"] = {"value": value, "evidence": [quote], "confidence": "high"}
    if len(dates) > 1:
        conflicts.append("Retained sources state different application deadlines.")
    flat = " ".join(text.split())
    for phrase in re.finditer(r"\b(?:(?:strong|distinctive|individual)\s+(?:creative|artistic)\s+(?:signature|voice)|(?:refined|strong|mature)\s+artistry|improvisational\s+(?:playfulness|skills?|ability))\b", flat, re.I):
        prefix = flat[max(0, phrase.start() - 160):phrase.start()]
        if not re.search(r"\b(requirements?|require[ds]?|seeking|must|with)\b", prefix, re.I):
            continue
        criteria = result.setdefault("requirements", {}).setdefault("experience", {}).setdefault("criteria", [])
        quote = phrase.group()
        if not any(quote.casefold() in str(item.get("value", "")).casefold() for item in criteria if isinstance(item, dict)):
            criteria.append({"value": quote[0].upper() + quote[1:], "evidence": [quote], "confidence": "high"})
    enrollment = re.search(rf"\b(?:still|must\s+(?:be|remain))\s+enroll?ed\b.{{0,100}}?\bgraduating\s+in\s+{MONTH}\s+20\d{{2}}", flat, re.I)
    if enrollment:
        criteria = result.setdefault("requirements", {}).setdefault("experience", {}).setdefault("criteria", [])
        quote = enrollment.group()
        if not any(quote.casefold() in str(item.get("value", "")).casefold() for item in criteria if isinstance(item, dict)):
            criteria.append({"value": quote[0].upper() + quote[1:], "evidence": [quote], "confidence": "high"})
    for match in TECHNIQUE.finditer(flat):
        prefix = flat[max(0, match.start() - 140):match.start()]
        suffix = flat[match.end():match.end() + 50]
        if not re.search(r"\b(requirements?|require[ds]?|seeking|must|with|looking for)\b", prefix, re.I):
            continue
        if re.search(r"\b(not required|not necessary|do not require|preferred|desirable|optional)\b", prefix[-65:] + " " + suffix[:25], re.I):
            continue
        # Do not turn alternatives into two mandatory disciplines.
        if re.match(rf"\s+or\s+{NAMES}", suffix, re.I):
            continue
        requirements = result.setdefault("requirements", {}).setdefault("discipline_requirements", {})
        for name in re.finditer(NAMES, match["bracket"] or match["plain"], re.I):
            discipline = DISCIPLINES[name.group().lower()]
            if not known(requirements.get(discipline, {}).get("required_level")):
                requirements[discipline] = {"required_level": LEVELS[match["level"].lower()], "evidence": [match.group()], "confidence": "high", "assessment_basis": "explicit"}
    if conflicts:
        review["conflicts"] = list(dict.fromkeys(conflicts))
    return result
