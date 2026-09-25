"""StageViva Matching Engine.

Compares one Artist DNA profile against one Opportunity Intelligence
record and produces a strict structured matching result.

This module does NOT modify the Artist DNA or Opportunity Intelligence.
It only compares them.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import date
from typing import Any, Optional

from dotenv import load_dotenv
from openai import OpenAI

from match_schema import MATCH_SCHEMA


# Matching runs once per visible opportunity after a CV upload. Use the fast
# structured-output model by default so a performer is not left waiting while
# the feed is built. Deployments can override this without changing code.
MATCHING_MODEL = os.getenv("STAGEVIVA_MATCHING_MODEL", "gpt-4o-mini")
OPENAI_REQUEST_TIMEOUT_SECONDS = float(
    os.getenv("STAGEVIVA_MATCHING_TIMEOUT_SECONDS", "30")
)


# ============================================================
# OPENAI INSTRUCTIONS
# ============================================================

MATCHING_INSTRUCTIONS = """
You are StageViva's Matching Engine.

Your job is to compare ONE dancer's Artist DNA against ONE
professional opportunity's Opportunity Intelligence.

Your output will be used by StageViva to determine whether the
dancer should be shown or notified about the opportunity.

Your goal is NOT to make the dancer look better.

Your goal is:

"Based only on the supplied Artist DNA and Opportunity
Intelligence, how compatible is this dancer with this
opportunity?"

============================================================
CORE PRINCIPLES
============================================================

1. NEVER INVENT INFORMATION

Only use information contained in:

- ARTIST DNA
- OPPORTUNITY INTELLIGENCE

Do not use outside knowledge.

Do not assume information that is not present.

If information is missing, use:

"unknown"

or the appropriate unknown/neutral value.

============================================================
2. KEEP ARTIST AND OPPORTUNITY SEPARATE

Artist DNA describes the dancer.

Opportunity Intelligence describes the opportunity.

Never change the opportunity requirements because of the dancer.

Never change the dancer's level because of the opportunity.

The comparison happens ONLY after both have been independently
analysed.

============================================================
3. DISCIPLINE MATCHING

Compare the dancer's discipline levels with the opportunity's
required levels.

Examples:

Artist:
classical_ballet = high

Opportunity:
classical_ballet = high

This is a strong compatibility.

Example:

Artist:
classical_ballet = medium

Opportunity:
classical_ballet = very_high

This should reduce the match.

Example:

Artist:
contemporary = high

Opportunity:
contemporary = unknown

Do NOT penalise the dancer simply because the opportunity does
not specify a contemporary requirement.

Unknown requirements are not automatically low requirements.

Use the full score range when the evidence supports it. Do not default strong
dance opportunities to 85 simply because the artist has professional dance
experience. Different requirements, evidence, eligibility and missing skills
must create meaningfully different scores.

For an explicitly required core discipline, missing evidence is a real gap:
- High or very_high singing, acting, jazz, musical theatre, acrobatics or
  circus required, but absent from Artist DNA: do not score above 45.
- A medium requirement in one of those disciplines, but absent from Artist
  DNA: do not score above 55.
- A dancer's strength in another discipline must never be treated as evidence
  of singing, acting, jazz or musical theatre ability.

============================================================
4. REQUIREMENT SEVERITY

Pay particular attention to:

very_high requirements.

A very_high requirement represents an important standard.

If an opportunity requires very_high ballet and the artist is
only medium in ballet, this should be a meaningful negative
factor.

However, do not automatically make the entire opportunity
"not recommended" unless the requirement is clearly essential
and the mismatch is substantial.

============================================================
5. PROFESSIONAL EXPERIENCE

Compare:

Artist professional experience

against:

Opportunity professional experience requirements.

For example:

Artist:
current company dancer + previous professional company work

Opportunity:
several years of professional company experience

This is a strong positive factor if supported by the supplied
data.

Do not invent years of experience.

============================================================
6. REPERTOIRE

Compare the artist's repertoire with the opportunity's stated
repertoire requirements.

Relevant evidence can include:

- works
- roles
- choreographers
- company experience
- specific repertoire

Do not assume that performing one ballet automatically means
the dancer has experience in every classical role.

============================================================
7. TRAINING

Compare the artist's training against the opportunity's stated
training requirements.

Consider:

- professional dance training
- classical ballet training
- contemporary training
- relevant institutions
- qualifications

Do not automatically reject an artist because the opportunity
does not specify a training requirement.

============================================================
8. CAPABILITIES

Compare capabilities such as:

- partnering
- pointe
- improvisation
- acting
- singing
- choreography
- teaching
- special skills

Only use capabilities actually present in the Artist DNA or
required by the Opportunity Intelligence.

============================================================
9. PHYSICAL REQUIREMENTS

Physical requirements can be HARD constraints.

Compare:

- age
- height
- gender
- other explicit physical requirements

If the opportunity specifies a requirement and the Artist DNA
clearly conflicts with it:

mark the relevant status as "ineligible" or "conflict".

Do not invent physical requirements.

If no requirement exists:

do not penalise the artist.

============================================================
10. WORK RIGHTS / VISA

Work eligibility can be a major factor.

If the opportunity requires specific work authorization and the
Artist DNA clearly shows that the artist does not have it:

mark:

eligibility.status = "ineligible"

If the Artist DNA does not contain enough information:

use:

"possible_issue"

or:

"unknown"

Do not assume citizenship, visa status or work authorization.

When the artist explicitly records work rights for particular places, use that
information. For example, "Right to work in UK & Europe" does not establish a
right to work in the United States. A US role without stated visa support must
not be a strong match for that performer: use "possible_issue", explain the
visa uncertainty and keep the score meaningfully below comparable roles in
their confirmed work-rights region. Only use "ineligible" when the supplied
artist data explicitly says they cannot meet a stated requirement.

If the opportunity explicitly offers visa/work-permit sponsorship or support,
do not treat missing work rights as a conflict; state the support accurately.

============================================================
11. DATES AND AVAILABILITY

Compare opportunity dates with the artist's available dates ONLY
if availability information exists in the Artist DNA.

Do not invent availability.

If there is no artist availability information:

use "unknown".

For relative availability such as "Within 3 months", interpret it from the
explicit confirmation date supplied with the Artist DNA, not as a permanent
statement. The artist may still sensibly apply to an audition before they can
start work, especially when the contract begins later.

Keep application/audition dates separate from contract start dates:

- A near application deadline is not, by itself, an availability conflict.
- If the contract clearly starts before the artist's stated availability, make
  this a soft negative and explain it. Do not make it ineligible unless the
  supplied dates make the conflict certain and material.
- If the contract starts at or after the artist's estimated availability,
  treat the timing as compatible even when the audition is sooner.

============================================================
12. PREFERENCES

Compare the opportunity against the artist's stated preferences.

Relevant preferences include:

- target roles
- preferred disciplines
- preferred countries
- preferred companies
- contract preferences
- salary expectations
- relocation preferences

Preferences should influence the recommendation but should NOT
override hard eligibility requirements.

For example:

A dancer preferring Germany should receive a positive preference
factor for a German opportunity.

But preference for Germany does not make an otherwise impossible
work-rights situation eligible.

Explicit preferred locations are a meaningful ranking factor. A performer who
prefers the UK and Europe, even when open to international relocation, should
normally see equivalent UK/Europe roles ahead of US roles. International
opportunities can still be shown when artistically suitable; they simply must
not receive the same top ranking without a location or visa advantage.

============================================================
13. SCORE

Produce an overall integer match score from 0 to 100.

The score represents overall compatibility between the artist
and opportunity.

Use the following general interpretation:

90–100:
Exceptional compatibility.

80–89:
Strong compatibility.

70–79:
Good compatibility.

60–69:
Moderate compatibility.

40–59:
Weak compatibility.

0–39:
Very weak compatibility.

Do not mechanically calculate the score from one field.

Consider the complete evidence available.

============================================================
14. HARD CONSTRAINTS

Certain requirements can strongly limit the maximum score.

Examples:

- explicit incompatible age requirement
- explicit incompatible height requirement
- explicit incompatible gender requirement
- confirmed lack of required work authorization
- confirmed unavailable audition/contract dates

When a clear hard conflict exists, the score should reflect it
strongly.

Do not ignore hard requirements simply because the dancer has
excellent artistic compatibility.

============================================================
15. UNKNOWN INFORMATION

Missing information must NOT automatically become a negative.

For example:

Opportunity height requirement = unknown

Artist height = 176 cm

Do not penalise the artist.

Similarly:

Opportunity salary = unknown

Do not assume the salary is poor.

Unknown means unknown.

============================================================
16. MATCH LEVEL

Use:

excellent
strong
moderate
weak
very_weak
unknown

Base this on the overall compatibility and evidence.

Suggested interpretation:

excellent:
90–100

strong:
80–89

moderate:
60–79

weak:
40–59

very_weak:
0–39

unknown:
Only when there is genuinely insufficient information to
meaningfully assess the opportunity.

============================================================
17. RECOMMENDATION

Use:

strong_match
good_match
possible_match
weak_match
not_recommended
unknown

A recommendation should consider both:

- compatibility
- important risks or gaps

A dancer can have a high artistic match but still have an
eligibility problem.

============================================================
18. STRENGTHS

Identify the strongest reasons the dancer fits.

Examples:

"Professional company experience matches the opportunity's
professional experience requirement."

"High classical ballet level matches the opportunity's high
classical ballet requirement."

"Contemporary experience matches the opportunity's contemporary
requirement."

Every strength must be supported by supplied evidence.

============================================================
19. GAPS

Identify meaningful gaps or risks.

Examples:

"The opportunity requires very_high classical ballet while the
Artist DNA indicates high classical ballet."

"Work authorization is not established in the Artist DNA."

Do NOT list every unknown field as a gap.

Only include meaningful limitations.

============================================================
20. MATCH REASONS

Generate concise reasons that explain the match.

These will later be shown to the dancer.

Avoid generic statements such as:

"Good match because Dance."

Instead say something specific:

"Your high classical ballet level aligns with the opportunity's
high classical ballet requirement."

"Your professional company experience matches the requirement
for professional stage experience."

"Your contemporary experience aligns with the requested
contemporary variation."

============================================================
21. USER-FACING LANGUAGE

The notification must be understandable to a dancer.

Do not expose internal technical language such as:

"schema"
"embedding"
"feature vector"
"canonical discipline"

Use natural language.

Good:

"Your professional ballet experience and contemporary training
make you a strong match for this audition."

Bad:

"Artist capability vector matches opportunity feature vector."

============================================================
22. NOTIFICATION

Set:

should_notify = true

when the opportunity is a meaningful match worth showing to the
dancer.

Do not notify solely because an opportunity exists.

A strong match should normally trigger notification.

A weak or very weak match should normally not trigger a
notification.

However, use the complete evidence and eligibility information.

The exact notification threshold may later be configured by
StageViva.

============================================================
23. NO DANCER MATCHING FROM OUTSIDE INFORMATION

Do not use:

- general knowledge about the dancer
- information from previous conversations
- external websites
- assumptions about dance companies
- assumptions about industry standards

Only the supplied Artist DNA and Opportunity Intelligence may be
used.

============================================================
24. OUTPUT

Return ONLY structured JSON matching MATCH_SCHEMA.

Do not return markdown.

Do not return explanations outside the JSON.
"""


# ============================================================
# ERRORS
# ============================================================

class MatchEngineError(RuntimeError):
    """Raised when the matching engine cannot complete."""


_LEVEL_RANK = {"unknown": 0, "low": 1, "medium": 2, "high": 3, "very_high": 4}
_CORE_CROSS_DISCIPLINES = frozenset({
    "singing", "acting", "jazz", "musical_theatre", "acrobatics", "circus",
})


def _normalise_artist_gender(value: Any) -> str:
    raw = str(value or "").strip().lower()
    aliases = {
        "male": "male", "man": "male", "men": "male", "m": "male",
        "female": "female", "woman": "female", "women": "female", "f": "female",
        "non-binary": "non_binary", "nonbinary": "non_binary", "non binary": "non_binary",
    }
    return aliases.get(raw, "unknown")


def _required_genders(opportunity: dict[str, Any]) -> set[str]:
    """Return a restricted gender set only when a listing is unambiguous.

    Listings for "all genders", "male and female", or mixed-gender duos must
    remain open to every performer. A single-gender requirement, however, is
    a hard eligibility constraint rather than a soft AI preference.
    """
    physical = opportunity.get("requirements", {}).get("physical", {})
    field = physical.get("gender_requirement", {}) if isinstance(physical, dict) else {}
    raw = field.get("value", field) if isinstance(field, dict) else field
    text = str(raw or "").strip().lower()
    if not text or text in {"unknown", "not specified", "n/a"}:
        return set()
    if any(phrase in text for phrase in (
        "all gender", "any gender", "both gender", "male and female", "female and male",
        "men and women", "women and men", "mixed gender", "mixed-gender",
    )):
        return set()
    genders: set[str] = set()
    if re.search(r"\b(?:male|men|man)\b", text):
        genders.add("male")
    if re.search(r"\b(?:female|women|woman)\b", text):
        genders.add("female")
    if re.search(r"\b(?:non[ -]?binary)\b", text):
        genders.add("non_binary")
    # More than one identified gender is not a single-gender exclusion.
    return genders if len(genders) == 1 else set()


def _apply_gender_requirement_guard(
    artist_dna: dict[str, Any], opportunity: dict[str, Any], result: dict[str, Any],
) -> dict[str, Any]:
    """Cap explicit gender conflicts so unsuitable roles are still visible but clear."""
    required = _required_genders(opportunity)
    artist_gender = _normalise_artist_gender(artist_dna.get("identity", {}).get("gender"))
    if not required or artist_gender == "unknown" or artist_gender in required:
        return result

    required_label = next(iter(required)).replace("_", " ")
    overall = result.setdefault("overall", {})
    overall["match_score"] = min(int(overall.get("match_score", 0) or 0), 20)
    overall["match_level"] = "very_weak"
    overall["recommendation"] = "not_recommended"
    overall["summary"] = f"This role is explicitly seeking a {required_label} performer."
    physical_match = result.setdefault("physical_match", {})
    physical_match["gender"] = {
        "status": "ineligible",
        "evidence": [f"The opportunity explicitly requires a {required_label} performer."],
    }
    gap_text = f"This opportunity is explicitly seeking a {required_label} performer, which does not match your profile setting."
    gaps = result.setdefault("gaps", [])
    if not any(gap_text == item.get("description") for item in gaps if isinstance(item, dict)):
        gaps.append({
            "factor": "Gender requirement",
            "description": gap_text,
            "evidence": [],
            "severity": "major",
        })
    return result


_EUROPEAN_UNION_COUNTRIES = frozenset({
    "austria", "belgium", "bulgaria", "croatia", "cyprus", "czechia", "czech republic",
    "denmark", "estonia", "finland", "france", "germany", "greece", "hungary", "ireland",
    "italy", "latvia", "lithuania", "luxembourg", "malta", "netherlands", "poland",
    "portugal", "romania", "slovakia", "slovenia", "spain", "sweden",
})


def _field_text(document: dict[str, Any], *path: str) -> str:
    value: Any = document
    for key in path:
        if not isinstance(value, dict):
            return ""
        value = value.get(key)
    if isinstance(value, dict):
        value = value.get("value", "")
    return str(value or "").strip()


def _location_regions(value: Any) -> set[str]:
    """Map only explicit country/region wording to work-rights regions."""
    text = " ".join(str(part) for part in value) if isinstance(value, list) else str(value or "")
    normalized = text.strip().lower()
    if not normalized:
        return set()
    regions: set[str] = set()
    if any(term in normalized for term in ("worldwide", "world wide", "international", "open to all")):
        regions.add("worldwide")
    if any(term in normalized for term in ("united kingdom", " uk", "u.k.", "britain", "england", "scotland", "wales", "northern ireland")):
        regions.add("uk")
    if any(term in normalized for term in ("european union", "europe", " eu", "e.u.")) or normalized in _EUROPEAN_UNION_COUNTRIES:
        regions.add("eu")
    if any(term in normalized for term in ("united states", "usa", "u.s.a", "america", "new york", "puerto rico", "canada")):
        regions.add("north_america")
    if any(term in normalized for term in ("australia", "new zealand", "asia", "pacific", "japan", "singapore")):
        regions.add("asia_pacific")
    return regions


def _has_stated_visa_support(opportunity: dict[str, Any]) -> bool:
    text = " ".join((
        _field_text(opportunity, "requirements", "work_rights_or_visa"),
        _field_text(opportunity, "contract_and_compensation", "visa_support"),
    )).lower()
    if not text or text in {"unknown", "not specified", "n/a"}:
        return False
    return any(term in text for term in (
        "visa support", "visa sponsorship", "sponsor", "work permit support", "permit support",
        "visa provided", "work permit provided",
    ))


def _add_gap(result: dict[str, Any], *, factor: str, description: str) -> None:
    gaps = result.setdefault("gaps", [])
    if not any(description == item.get("description") for item in gaps if isinstance(item, dict)):
        gaps.append({"factor": factor, "description": description, "evidence": [], "severity": "major"})


def _apply_location_and_work_rights_guard(
    artist_dna: dict[str, Any], opportunity: dict[str, Any], result: dict[str, Any],
) -> dict[str, Any]:
    """Keep overseas roles from outranking work the performer can actually take.

    An artist selecting UK and EU work rights has not necessarily stated that a
    US visa is impossible, so this is a strong cap rather than a false hard
    rejection. A listing that explicitly offers visa support remains visible,
    but cannot be presented as an uncomplicated top match.
    """
    eligibility = artist_dna.get("eligibility", {}) if isinstance(artist_dna, dict) else {}
    preferences = artist_dna.get("preferences", {}) if isinstance(artist_dna, dict) else {}
    rights = _location_regions(eligibility.get("work_rights") if isinstance(eligibility, dict) else [])
    preferred = _location_regions(
        (preferences.get("preferred_locations") if isinstance(preferences, dict) else None)
        or (preferences.get("preferred_countries") if isinstance(preferences, dict) else None)
    )
    country = _field_text(opportunity, "location", "country")
    city = _field_text(opportunity, "location", "city")
    contract_type = _field_text(opportunity, "contract_and_compensation", "contract_type").lower()
    opportunity_regions = _location_regions(f"{city} {country}")
    if not opportunity_regions or "worldwide" in opportunity_regions or "cruise" in contract_type:
        return result

    overall = result.setdefault("overall", {})
    stated_visa_support = _has_stated_visa_support(opportunity)
    outside_confirmed_rights = bool(rights and "worldwide" not in rights and not (rights & opportunity_regions))
    outside_preferences = bool(preferred and "worldwide" not in preferred and not (preferred & opportunity_regions))

    if outside_confirmed_rights:
        cap = 70 if stated_visa_support else 40
        if int(overall.get("match_score", 0) or 0) > cap:
            overall["match_score"] = cap
        overall["match_level"] = "moderate" if stated_visa_support else "weak"
        overall["recommendation"] = "possible_match" if stated_visa_support else "weak_match"
        place = country or city or "this location"
        if stated_visa_support:
            message = f"This role is outside your confirmed work-rights regions, but the listing states visa or work-permit support."
        else:
            message = f"This role is in {place}, outside your confirmed work-rights regions, and the listing does not state visa support."
        overall["summary"] = message
        match_eligibility = result.setdefault("eligibility", {})
        match_eligibility["status"] = "possible_issue"
        match_eligibility["work_rights"] = {"status": "possible_issue", "evidence": [message]}
        _add_gap(result, factor="Work rights and visa", description=message)
        return result

    if outside_preferences:
        cap = 70
        if int(overall.get("match_score", 0) or 0) > cap:
            overall["match_score"] = cap
        overall["match_level"] = "moderate"
        overall["recommendation"] = "possible_match"
        place = country or city or "this location"
        message = f"This role is outside your preferred work locations ({place}), so it is ranked below comparable local opportunities."
        _add_gap(result, factor="Preferred work location", description=message)
    return result


def _calibrate_evidence_score(result: dict[str, Any]) -> dict[str, Any]:
    """Resolve coarse model score ties using the structured evidence it gave.

    This is deliberately deterministic: more supported reasons and strengths
    raise confidence in a fit, while documented gaps reduce it. It prevents a
    row of visually identical 85% results without inventing variation.
    """
    overall = result.setdefault("overall", {})
    original = int(overall.get("match_score", 0) or 0)
    reasons = len([item for item in result.get("match_reasons", []) if isinstance(item, dict)])
    strengths = len([item for item in result.get("strengths", []) if isinstance(item, dict)])
    gaps = len([item for item in result.get("gaps", []) if isinstance(item, dict)])
    adjustment = max(-10, min(6, (reasons * 2) + strengths - (gaps * 3)))
    overall["match_score"] = max(0, min(100, original + adjustment))
    return result


def _apply_requirement_guards(
    artist_dna: dict[str, Any],
    opportunity: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Prevent a strong dancer profile masking an absent essential skill.

    This is a general evidence rule, not a hand-tuned adjustment for individual
    auditions. The model still performs the full comparison and explanation.
    """
    artist_levels = {
        str(item.get("discipline", "")).strip().lower(): str(item.get("level", "unknown")).lower()
        for item in artist_dna.get("disciplines", [])
        if isinstance(item, dict)
    }
    requirements = opportunity.get("requirements", {}).get("discipline_requirements", {})
    absent_essential: list[tuple[str, str]] = []
    absent_other_required: list[tuple[str, str]] = []
    if isinstance(requirements, dict):
        for discipline, requirement in requirements.items():
            requirement = requirements.get(discipline, {})
            if not isinstance(requirement, dict):
                continue
            required_level = str(requirement.get("required_level", "unknown")).lower()
            artist_level = artist_levels.get(discipline, "unknown")
            if _LEVEL_RANK.get(required_level, 0) >= 2 and _LEVEL_RANK.get(artist_level, 0) == 0:
                target = absent_essential if discipline in _CORE_CROSS_DISCIPLINES else absent_other_required
                target.append((discipline, required_level))

    if not absent_essential and not absent_other_required:
        return result

    highest_cross_requirement = max(
        (_LEVEL_RANK.get(level, 0) for _, level in absent_essential), default=0,
    )
    highest_other_requirement = max(
        (_LEVEL_RANK.get(level, 0) for _, level in absent_other_required), default=0,
    )
    if highest_cross_requirement >= 3:
        cap = 45
    elif highest_cross_requirement:
        cap = 55
    else:
        # An explicit high-level technical requirement still matters. It is a
        # weaker cap than a missing singer/actor requirement because a listing
        # can sometimes assess related training at the audition.
        cap = 55 if highest_other_requirement >= 3 else 65
    overall = result.setdefault("overall", {})
    score = int(overall.get("match_score", 0) or 0)
    if score <= cap:
        return result

    overall["match_score"] = cap
    overall["match_level"] = "weak" if cap <= 45 else "moderate"
    overall["recommendation"] = "weak_match" if cap <= 45 else "possible_match"
    missing = absent_essential + absent_other_required
    missing_names = ", ".join(name.replace("_", " ") for name, _ in missing)
    gap_text = f"This opportunity explicitly requires {missing_names}, which is not evidenced in the current Artist DNA."
    gaps = result.setdefault("gaps", [])
    if not any(gap_text == item.get("description") for item in gaps if isinstance(item, dict)):
        gaps.append({
            "factor": "Required skill not evidenced",
            "description": gap_text,
            "evidence": [],
            "severity": "major" if cap <= 45 else "moderate",
        })
    return result


# ============================================================
# VALIDATION
# ============================================================

def _validate_input(
    artist_dna: dict[str, Any],
    opportunity: dict[str, Any],
) -> None:
    """Perform lightweight input validation."""

    if not isinstance(artist_dna, dict):
        raise MatchEngineError(
            "artist_dna must be a dictionary."
        )

    if not isinstance(opportunity, dict):
        raise MatchEngineError(
            "opportunity must be a dictionary."
        )


# ============================================================
# MATCH
# ============================================================

def match_artist_to_opportunity(
    artist_dna: dict[str, Any],
    opportunity: dict[str, Any],
    client: Optional[OpenAI] = None,
) -> dict[str, Any]:
    """
    Compare one Artist DNA profile against one opportunity.

    Returns structured JSON matching MATCH_SCHEMA.
    """

    _validate_input(
        artist_dna,
        opportunity,
    )

    load_dotenv()

    preferences = artist_dna.get("preferences", {}) if isinstance(artist_dna, dict) else {}
    availability = preferences.get("availability", "unknown") if isinstance(preferences, dict) else "unknown"
    confirmed_at = preferences.get("availability_confirmed_at", "unknown") if isinstance(preferences, dict) else "unknown"
    prompt = (
        "MATCHING DATE CONTEXT\n"
        "=====================\n"
        f"Today: {date.today().isoformat()}\n"
        f"Artist availability: {availability}\n"
        f"Artist last confirmed this availability: {confirmed_at}\n\n"
        "ARTIST DNA\n"
        "==========\n"
        f"{json.dumps(artist_dna, ensure_ascii=False, indent=2)}\n\n"
        "OPPORTUNITY INTELLIGENCE\n"
        "========================\n"
        f"{json.dumps(opportunity, ensure_ascii=False, indent=2)}\n"
    )

    response = (
        client or OpenAI(timeout=OPENAI_REQUEST_TIMEOUT_SECONDS, max_retries=1)
    ).responses.create(
        model=MATCHING_MODEL,
        # The strict match schema contains several explanation fields. This
        # ceiling avoids truncated JSON while remaining far below the former
        # unrestricted slow-model requests.
        max_output_tokens=2_000,
        instructions=MATCHING_INSTRUCTIONS,
        input=prompt,
        text={
            "format": {
                "type": "json_schema",
                "name": "stageviva_match",
                "strict": True,
                "schema": MATCH_SCHEMA,
            }
        },
    )

    try:
        calibrated = _calibrate_evidence_score(json.loads(response.output_text))
        guarded = _apply_requirement_guards(artist_dna, opportunity, calibrated)
        guarded = _apply_gender_requirement_guard(artist_dna, opportunity, guarded)
        return _apply_location_and_work_rights_guard(artist_dna, opportunity, guarded)

    except json.JSONDecodeError as error:
        raise MatchEngineError(
            "OpenAI returned invalid structured matching output."
        ) from error


# ============================================================
# COMMAND LINE
# ============================================================

def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "Compare StageViva Artist DNA against "
            "Opportunity Intelligence."
        )
    )

    parser.add_argument(
        "artist_json",
        help="Path to Artist DNA JSON file.",
    )

    parser.add_argument(
        "opportunity_json",
        help="Path to Opportunity Intelligence JSON file.",
    )

    args = parser.parse_args()

    try:
        with open(
            args.artist_json,
            "r",
            encoding="utf-8",
        ) as file:
            artist_dna = json.load(file)

        with open(
            args.opportunity_json,
            "r",
            encoding="utf-8",
        ) as file:
            opportunity = json.load(file)

    except FileNotFoundError as error:
        raise MatchEngineError(
            f"Could not find input file: {error.filename}"
        ) from error

    except json.JSONDecodeError as error:
        raise MatchEngineError(
            f"Invalid JSON input: {error}"
        ) from error

    result = match_artist_to_opportunity(
        artist_dna,
        opportunity,
    )

    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        )
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main() 
