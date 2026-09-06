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
    """Keep career opportunities; exclude general education and training enrolment.

    A trainee, apprentice or young-artist position remains eligible because it is
    a transition into professional work. A school, class, diploma or intensive
    does not belong in the shared audition-matching feed.
    """
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
        "pre-professional company", "graduate company", "paid graduate programme",
        "paid graduate program",
    )
    career_markers = (
        "paid", "employment", "contract", "company position", "company dancer",
        "job", "casting", "engagement", "professional company",
        *transition_markers,
    )
    education_markers = (
        "school", "summer intensive", "summer school", "weekend class", "classes",
        "course", "diploma", "degree", "curriculum", "associate programme",
        "associate program", "pre-vocational", "tuition", "training programme",
        "training program", "teacher training", "admission",
    )
    is_training = any(marker in text for marker in education_markers)
    if is_training:
        # "Trainee" alone is often school language. Retain only an explicit
        # paid/apprenticeship/young-artist professional pathway.
        professional_pathway_markers = (
            "paid", "employment", "apprenticeship", "young artist", "young-artist",
            "pre-professional company", "graduate company", "company contract",
        )
        if not any(marker in text for marker in professional_pathway_markers):
            return False
    if any(marker in text for marker in career_markers):
        return True
    return not is_training
