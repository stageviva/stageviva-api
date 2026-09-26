"""Product eligibility rules for opportunities shown in StageViva."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse


DIRECTORY_DOMAINS = frozenset({
    "balletplaces.com", "danceeurope.net", "ballee.co",
    "entertainersworldwidejobs.com", "allcasting.com",
    # Instagram posts are discovery evidence. A caption must contain a real
    # public application destination before it can be shown to performers.
    "instagram.com", "l.instagram.com",
})


def _value(opportunity: dict[str, Any], *path: str) -> str:
    current: Any = opportunity
    for key in path:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    if isinstance(current, dict):
        current = current.get("value", "")
    return str(current or "")


def _is_verified_public_url(url: str) -> bool:
    """A directory page is discovery evidence, never the final apply link."""
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower().removeprefix("www.")
    return parsed.scheme in {"http", "https"} and bool(host) and host not in DIRECTORY_DOMAINS


def has_verified_official_application_url(opportunity: dict[str, Any], listing_url: str) -> bool:
    """Return whether performers have a real company/casting/agency destination.

    New listings may arrive through a directory, but they are only live once
    the discovery step finds an off-directory official destination. Direct
    company, casting and agency listings are themselves valid destinations.
    """
    candidates = (
        _value(opportunity, "application", "application_url"),
        _value(opportunity, "source", "official_url"),
        listing_url,
    )
    return any(_is_verified_public_url(candidate) for candidate in candidates)


def is_stageviva_eligible(opportunity: dict[str, Any]) -> bool:
    """Allow paid work and genuine transition roles, never general study."""
    text = " ".join((
        _value(opportunity, "identity", "title"),
        _value(opportunity, "identity", "opportunity_type"),
        _value(opportunity, "identity", "description"),
        _value(opportunity, "contract_and_compensation", "contract_type"),
        _value(opportunity, "contract_and_compensation", "compensation"),
    )).lower()

    # A transition programme is allowed only when it is explicitly a pathway
    # into paid/professional work. General schools and classes never belong in
    # the performer opportunity feed.
    paid_work_markers = (
        "paid employment", "employment", "employment contract", "contract",
        "company position", "company dancer", "company artist", "vacancy", "job",
        "engagement", "professional contract", "touring contract", "cruise contract",
        "paid casting", "paid role",
    )
    # These describe a credible step into professional work. A bare phrase such
    # as "trainee programme" is not enough: many schools use that language for
    # ordinary tuition.
    qualifying_transition_markers = (
        "company trainee", "trainee company", "professional trainee",
        "professional apprenticeship", "company apprenticeship", "young artist programme",
        "young artist program", "graduate company", "pre-professional company",
        "pre professional company", "paid graduate programme", "paid graduate program",
    )
    agency_markers = (
        "talent representation", "agency representation", "talent agency",
        "dance agency", "casting agency",
    )
    education_markers = (
        "school", "academy", "academies", "summer intensive", "summer school", "weekend class", "classes",
        "course", "diploma", "degree", "curriculum", "associate programme",
        "associate program", "pre-vocational", "tuition", "training programme",
        "training program", "teacher training", "admission", "conservatoire",
        "conservatory", "masterclass", "workshop", "open class",
    )
    is_training = any(marker in text for marker in education_markers)
    if is_training:
        # A study provider sometimes calls an ordinary course a "trainee"
        # programme. "Paid tuition" also does not make a course a job. Retain
        # only explicit company or employment-transition wording.
        professional_pathway_markers = (
            *paid_work_markers, *qualifying_transition_markers,
        )
        if not any(marker in text for marker in professional_pathway_markers):
            return False
    # Unknown listings no longer pass by default. StageViva is a career feed,
    # so each listing must identify professional work, a qualifying transition
    # pathway, or an explicit professional-representation call.
    return any(marker in text for marker in (
        *paid_work_markers, *qualifying_transition_markers, *agency_markers,
    ))
