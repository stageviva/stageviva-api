"""Strict Structured Outputs schema for StageViva Opportunity Intelligence."""

LEVELS = ["very_high", "high", "medium", "low", "unknown"]
CONFIDENCE_LEVELS = LEVELS

# This shared vocabulary makes matching predictable. An opportunity may still
# report additional disciplines in ``other_discipline_requirements``.
CANONICAL_DISCIPLINES = [
    "classical_ballet", "contemporary", "jazz", "commercial", "hip_hop",
    "musical_theatre", "tap", "ballroom", "latin", "character", "folk",
    "acrobatics", "circus", "partnering", "improvisation", "singing", "acting",
]


def _string_array():
    return {"type": "array", "items": {"type": "string"}}


def _field(value_schema):
    """A source-backed value used throughout the extraction result."""
    return {
        "type": "object",
        "properties": {
            "value": value_schema,
            "evidence": _string_array(),
            "confidence": {"type": "string", "enum": CONFIDENCE_LEVELS},
        },
        "required": ["value", "evidence", "confidence"],
        "additionalProperties": False,
    }


def _object(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


STRING = {"type": "string"}
BOOL = {"type": "boolean"}

DISCIPLINE_REQUIREMENT = _object({
    "required_level": {"type": "string", "enum": LEVELS},
    "evidence": _string_array(),
    "confidence": {"type": "string", "enum": CONFIDENCE_LEVELS},
    "assessment_basis": {
        "type": "string",
        "enum": ["explicit", "inferred_from_explicit_evidence", "not_specified"],
    },
})

OPPORTUNITY_SCHEMA = _object({
    "identity": _object({
        "title": _field(STRING),
        "opportunity_type": _field(STRING),
        "organisation": _field(STRING),
        "description": _field(STRING),
    }),
    "requirements": _object({
        "discipline_requirements": _object({
            **{discipline: DISCIPLINE_REQUIREMENT for discipline in CANONICAL_DISCIPLINES},
            "other_discipline_requirements": {
                "type": "array",
                "items": _object({"discipline": STRING, **DISCIPLINE_REQUIREMENT["properties"]}),
            },
        }),
        "styles": _field(_string_array()),
        "techniques": _field(_string_array()),
        "experience": _object({
            "required_level": _field(STRING),
            "professional_experience_required": _field(BOOL),
            "roles_or_ranks": _field(_string_array()),
        }),
        "physical": _object({
            "age_requirement": _field(STRING),
            "height_requirement": _field(STRING),
            "gender_requirement": _field(STRING),
            "other_requirements": _field(_string_array()),
        }),
        "languages": _field(_string_array()),
        "work_rights_or_visa": _field(STRING),
    }),
    "dates": _object({
        "published": _field(STRING),
        "application_deadline": _field(STRING),
        "audition_dates": _field(_string_array()),
        "contract_start": _field(STRING),
        "contract_end": _field(STRING),
    }),
    "location": _object({
        "country": _field(STRING),
        "city": _field(STRING),
        "venue": _field(STRING),
        "audition_locations": _field(_string_array()),
        "remote_application": _field(BOOL),
    }),
    "contract_and_compensation": _object({
        "contract_type": _field(STRING), "duration": _field(STRING),
        "compensation": _field(STRING), "accommodation": _field(STRING),
        "travel": _field(STRING), "visa_support": _field(STRING),
    }),
    "application": _object({
        "method": _field(STRING), "application_url": _field(STRING),
        "email": _field(STRING), "materials": _field(_string_array()),
        "audition_fee": _field(STRING),
    }),
    "source": _object({
        "discovery_source": _field(STRING), "discovery_url": _field(STRING),
        "official_source": _field(STRING), "official_url": _field(STRING),
        "source_type": _field(STRING), "retrieved_at": _field(STRING),
    }),
    "status": _object({
        "value": {"type": "string", "enum": ["active", "closing_soon", "closed", "cancelled", "unknown"]},
        "evidence": _string_array(),
        "confidence": {"type": "string", "enum": CONFIDENCE_LEVELS},
    }),
    "missing_information": _string_array(),
    "overall_confidence": {"type": "string", "enum": CONFIDENCE_LEVELS},
})
