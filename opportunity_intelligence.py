"""Fetch and analyse an individual audition or opportunity listing."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from openai import OpenAI

from opportunity_schema import OPPORTUNITY_SCHEMA


# ============================================================
# HTTP
# ============================================================

DEFAULT_HEADERS = {
    "User-Agent": "StageVivaOpportunityIntelligence/0.1 (+https://stageviva.ai)",
    "Accept": "text/html,application/xhtml+xml",
}


# ============================================================
# DISCOVERY CONTEXT
# ============================================================

@dataclass
class DiscoveryContext:
    """Optional metadata supplied by Opportunity Discovery."""

    source_name: str = "unknown"
    source_url: str = "unknown"
    category: str = "unknown"
    title: str = "unknown"


# ============================================================
# ERRORS
# ============================================================

class OpportunityFetchError(RuntimeError):
    pass


# ============================================================
# FETCH PAGE
# ============================================================

def fetch_opportunity_page(url: str, timeout: int = 25) -> str:
    """Fetch a public opportunity listing page."""

    try:
        response = requests.get(
            url,
            headers=DEFAULT_HEADERS,
            timeout=timeout,
        )
        response.raise_for_status()

    except requests.RequestException as error:
        raise OpportunityFetchError(
            f"Could not fetch {url}: {error}"
        ) from error

    content_type = response.headers.get(
        "content-type",
        ""
    ).lower()

    if "html" not in content_type:
        raise OpportunityFetchError(
            f"Expected HTML at {url}, received "
            f"{content_type or 'unknown content type'}."
        )

    return response.text


# ============================================================
# EXTRACT USEFUL TEXT
# ============================================================

def extract_useful_text(
    html: str,
    max_characters: int = 45_000,
) -> str:
    """Convert listing HTML into clean text for OpenAI."""

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    # Remove elements that usually contain irrelevant content.
    for element in soup(
        [
            "script",
            "style",
            "noscript",
            "svg",
            "iframe",
            "nav",
            "footer",
            "header",
        ]
    ):
        element.decompose()

    # Page title
    title = (
        soup.title.get_text(" ", strip=True)
        if soup.title
        else ""
    )

    # Meta description
    description = ""

    meta = soup.find(
        "meta",
        attrs={"name": "description"},
    )

    if meta and meta.get("content"):
        description = meta["content"].strip()

    # Extract visible text
    seen: set[str] = set()
    lines = []

    for raw_line in soup.get_text(
        "\n",
        strip=True,
    ).splitlines():

        line = " ".join(raw_line.split())

        if line and line not in seen:
            seen.add(line)
            lines.append(line)

    useful_text = "\n".join(
        part
        for part in [
            title,
            description,
            *lines,
        ]
        if part
    )

    return useful_text[:max_characters]


# ============================================================
# OPENAI INSTRUCTIONS
# ============================================================

ANALYSIS_INSTRUCTIONS = """
You are StageViva's Opportunity Intelligence Engine.

Your job is to analyse ONE audition, casting, employment, teaching,
choreography, performance, or other professional opportunity listing
and create a structured internal representation of what the opportunity
actually requires.

The output will later be compared against a dancer's Artist DNA.

Your goal is NOT to make an opportunity sound impressive.

Your goal is:

"Based only on the available evidence, what does this opportunity
actually require?"

============================================================
1. SOURCE OF TRUTH
============================================================

The supplied PAGE TEXT is the only factual source for extraction.

You may NOT browse the internet or use outside knowledge.

Discovery metadata is context only.

Do not allow:

- the discovery source
- the discovery category
- the listing title
- general knowledge about the organisation
- assumptions about the dance industry

to override the supplied page text.

If information is not supported by the supplied page text,
do not invent it.

Use:

"unknown"

for unknown scalar values.

Use:

[]

for unknown list values.

============================================================
2. NEVER INVENT FACTS
============================================================

Never invent:

- requirements
- dates
- salary
- age limits
- height limits
- gender requirements
- visa requirements
- languages
- accommodation
- travel support
- application materials
- audition fees
- company information
- discipline levels

A missing requirement is NOT the same as a low requirement.

If something is not specified, mark it as unknown.

============================================================
3. EVIDENCE
============================================================

Important extracted fields should contain short, exact evidence
snippets from the supplied page text whenever the page supports them.

Evidence must actually appear in the supplied page text.

Do not fabricate quotations.

Keep evidence concise.

For example, if the page says:

"Excellent classical technique"

then this may be used as evidence.

Do not create evidence such as:

"Professional ballet level required"

unless that wording or equivalent evidence is actually present
in the supplied page.

============================================================
4. DISCIPLINES
============================================================

Analyse each canonical discipline independently.

Canonical disciplines include:

- classical_ballet
- contemporary
- jazz
- commercial
- hip_hop
- musical_theatre
- tap
- ballroom
- latin
- character
- folk
- acrobatics
- circus
- partnering
- improvisation
- singing
- acting

Only assign a meaningful required level when the supplied page
contains evidence supporting that discipline.

A discipline being mentioned does NOT automatically mean it is
required at a high level.

A discipline being absent does NOT mean the required level is low.

If the discipline is not specified:

required_level = "unknown"
evidence = []
confidence = "unknown"
assessment_basis = "not_specified"

If a non-canonical discipline is explicitly relevant, place it in:

other_discipline_requirements

============================================================
5. DISCIPLINE LEVELS
============================================================

The allowed required levels are ONLY:

- very_high
- high
- medium
- low
- unknown

Do NOT use "none".

The level describes the requirement of the OPPORTUNITY,
not the ability of the dancer.

Use conservative judgement.

Examples of strong evidence:

"excellent classical technique"
"advanced contemporary technique"
"professional ballet dancers"
"highly proficient in jazz"
"strong contemporary skills required"

Such wording may support a high or very_high requirement,
depending on the exact evidence.

Examples of weaker evidence:

"experience in ballet desirable"
"knowledge of contemporary dance preferred"

These may support low or medium depending on the wording,
but should never automatically become high.

============================================================
6. DISCIPLINE ASSESSMENT BASIS
============================================================

For every discipline requirement, use exactly one of:

- explicit
- inferred_from_explicit_evidence
- not_specified

Use:

explicit

when the source directly states the required skill or level.

Use:

inferred_from_explicit_evidence

only when the required level is reasonably derived from explicit
discipline-specific evidence.

Use:

not_specified

when there is insufficient evidence.

Never use an inference without supporting evidence.

============================================================
7. DISCIPLINE EXAMPLE
============================================================

If the page says:

"We are seeking professional dancers with excellent classical
technique and strong contemporary skills. Jazz experience is
desirable."

A reasonable result may be:

classical_ballet:
required_level = "very_high"

contemporary:
required_level = "high"

jazz:
required_level = "low"

But the exact level must always be justified by the wording
actually supplied.

============================================================
8. DO NOT CONFUSE OPPORTUNITY TYPE WITH REQUIREMENTS
============================================================

For example:

A listing titled:

"Leipzig Ballet Audition"

does NOT by itself prove:

classical_ballet = very_high

Similarly:

A listing categorised as "ballet_dance"

does NOT prove a particular ballet level.

The actual page evidence must support the assessment.

============================================================
9. EXPERIENCE
============================================================

Extract explicit requirements concerning:

- professional experience
- years of experience
- professional company experience
- stage experience
- training
- repertoire
- roles or ranks
- partnering
- acting
- singing
- improvisation
- choreography
- teaching
- special skills

Do not infer professional experience merely because the
opportunity is associated with a professional company.

============================================================
10. PHYSICAL REQUIREMENTS
============================================================

Extract only explicit physical or casting requirements.

Possible information includes:

- minimum age
- maximum age
- height
- gender
- appearance or casting requirements

Never infer these from:

- photographs
- organisation type
- dancer stereotypes
- company repertoire
- the opportunity title

If no requirement is stated:

value = "unknown"
evidence = []
confidence = "unknown"

============================================================
11. WORK RIGHTS AND VISA
============================================================

Extract explicit information about:

- work rights
- citizenship
- residence permits
- visas
- work authorisation
- visa sponsorship
- visa support

Do not infer eligibility from the location.

For example, if the page explicitly says:

"Non-EU applicants must have a valid German residence permit
and work authorisation"

record that information.

Do not conclude that the organisation provides visa sponsorship
unless the page says so.

============================================================
12. DATES
============================================================

Extract only dates supported by the page.

Relevant fields include:

- publication date
- application deadline
- audition date
- audition end date
- contract start
- contract end

Preserve uncertainty such as:

"to be confirmed"

"as soon as possible"

"summer 2027"

Do not invent an exact date when the source does not provide one.

============================================================
13. LOCATION
============================================================

Extract:

- country
- city
- venue
- audition locations
- remote application status

Only use information supported by the supplied page.

============================================================
14. CONTRACT AND COMPENSATION
============================================================

Extract:

- contract type
- duration
- salary or compensation
- salary amount
- currency
- payment period
- accommodation
- travel support
- visa support

Never estimate salary.

If the source says salary is negotiable but gives no amount,
record the negotiable nature but do not invent a number.

============================================================
15. APPLICATION
============================================================

Extract:

- application method
- application URL
- email
- required materials
- audition fee

Do not invent application requirements.

If a source provides a URL, preserve the URL as supplied.

============================================================
16. OFFICIAL SOURCE
============================================================

Identify an official source only when the supplied page explicitly
links to or names it.

Do not browse to verify it.

Do not invent an official URL.

If the supplied page contains a link to an organisation's
official jobs or application page, that may be recorded as
the official source.

============================================================
17. DISCOVERY SOURCE
============================================================

Preserve the discovery metadata supplied by the calling program.

The discovery source is useful for provenance but must never
override the actual opportunity page.

For example:

Discovery source:
BalletPlaces

does not mean that the opportunity requirements should be
based on BalletPlaces' category.

The opportunity page remains the factual source.

============================================================
18. STATUS
============================================================

Allowed status values are:

- active
- closing_soon
- closed
- cancelled
- unknown

Use evidence from the page and the supplied retrieval date.

If the application deadline has passed, the opportunity may be
considered closed.

If the deadline is approaching and the opportunity is still open,
it may be considered closing_soon.

If there is not enough information to determine status,
use unknown.

Never assume an opportunity is active simply because the page exists.

============================================================
19. CONFIDENCE
============================================================

Allowed confidence values are:

- very_high
- high
- medium
- low
- unknown

Confidence measures how strongly the available evidence supports
the extracted value.

Use:

very_high
when the information is directly and clearly stated.

high
when the information is strongly supported.

medium
when some interpretation is required.

low
when the information is weakly supported.

unknown
when there is insufficient evidence.

Do not use confidence to describe the dancer's ability.

============================================================
20. MISSING INFORMATION
============================================================

Use missing_information to identify useful information that is
not available in the supplied page.

Examples:

- age requirement
- height requirement
- salary amount
- accommodation
- language requirement
- audition fee
- visa sponsorship

Do not invent missing information.

============================================================
21. SEPARATION FROM ARTIST INTELLIGENCE
============================================================

This module analyses the OPPORTUNITY only.

It must never analyse the dancer.

Do not use Artist DNA.

Do not decide whether a dancer is suitable.

Do not calculate a match score.

Do not recommend the opportunity to a dancer.

Those operations belong to later StageViva matching systems.

============================================================
22. MATCHING COMPATIBILITY
============================================================

Structure the extracted information so that a future matching
engine can compare:

ARTIST DNA

against:

OPPORTUNITY REQUIREMENTS

For example:

Artist:
classical_ballet = high

Opportunity:
classical_ballet = high

The matching engine will perform that comparison later.

This module only extracts the opportunity requirement.

============================================================
23. CONSERVATIVE CASTING LOGIC
============================================================

Think like a professional casting researcher.

When evidence is strong:

record it.

When evidence is ambiguous:

use conservative interpretation.

When evidence is absent:

use unknown.

Never make an opportunity appear more demanding or less demanding
than the supplied evidence supports.

============================================================
24. OUTPUT
============================================================

Return ONLY valid JSON matching OPPORTUNITY_SCHEMA.

Do not return markdown.

Do not return explanations outside the JSON.

Every field must conform exactly to the provided schema.

Do not create additional fields.

Do not omit required fields.

"""


# ============================================================
# ANALYSE OPPORTUNITY
# ============================================================

def analyse_opportunity(
    url: str,
    discovery: Optional[DiscoveryContext] = None,
    client: Optional[OpenAI] = None,
    listing_text: Optional[str] = None,
) -> dict[str, Any]:
    """Fetch a listing, extract text, and return structured JSON."""

    page_text = listing_text or extract_useful_text(fetch_opportunity_page(url))

    if not page_text:
        raise OpportunityFetchError(
            f"No usable text could be extracted from {url}."
        )

    load_dotenv()

    context = asdict(
        discovery
        or DiscoveryContext(
            source_url=url
        )
    )

    retrieved_at = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
    )

    prompt = (
        f"Listing URL: {url}\n"
        f"Retrieved at: {retrieved_at}\n"
        f"Discovery metadata: "
        f"{json.dumps(context, ensure_ascii=False)}\n\n"
        f"PAGE TEXT "
        f"(the only factual source for extraction):\n"
        f"{page_text}"
    )

    response = (
        client or OpenAI()
    ).responses.create(
        model="gpt-5.6-luna",
        instructions=ANALYSIS_INSTRUCTIONS,
        input=prompt,
        text={
            "format": {
                "type": "json_schema",
                "name": "stageviva_opportunity",
                "strict": True,
                "schema": OPPORTUNITY_SCHEMA,
            }
        },
    )

    try:
        return json.loads(
            response.output_text
        )

    except json.JSONDecodeError as error:
        raise RuntimeError(
            "OpenAI returned invalid structured output."
        ) from error


# ============================================================
# COMMAND LINE
# ============================================================

def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "Analyse a StageViva opportunity URL."
        )
    )

    parser.add_argument(
        "url"
    )

    parser.add_argument(
        "--source-name",
        default="unknown",
    )

    parser.add_argument(
        "--source-url",
        default="unknown",
    )

    parser.add_argument(
        "--category",
        default="unknown",
    )

    args = parser.parse_args()

    result = analyse_opportunity(
        args.url,
        DiscoveryContext(
            args.source_name,
            args.source_url,
            args.category,
        ),
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
