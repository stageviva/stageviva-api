"""Product eligibility rules for opportunities shown in StageViva."""

from __future__ import annotations

from typing import Any


def _value(opportunity: dict[str, Any], *path: str) -> str:
    current: Any = opportunity
    for key in path:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    if isinstance(current, dict):
        current = current.get("value", "")
    return str(current or "")


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
    transition_markers = (
        "apprentice", "apprenticeship", "trainee", "young artist", "young-artist",
        "pre-professional", "pre professional", "graduate company",
        "paid graduate programme", "paid graduate program",
    )
    career_markers = (
        "paid", "employment", "contract", "company position", "company dancer",
        "job", "casting", "engagement", "professional company",
        *transition_markers,
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
        # programme. Retain only explicit career-transition wording.
        professional_pathway_markers = (
            "paid", "employment", "apprenticeship", "young artist", "young-artist",
            "pre-professional company", "pre professional company", "graduate company", "company contract",
        )
        if not any(marker in text for marker in professional_pathway_markers):
            return False
    # Unknown listings no longer pass by default. StageViva is a career feed,
    # so each listing must identify paid/professional work or a qualifying
    # trainee, apprenticeship or pre-professional pathway.
    return any(marker in text for marker in career_markers)
