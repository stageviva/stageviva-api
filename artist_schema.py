"""Strict Structured Outputs schema for StageViva Artist Intelligence."""

# ============================================================
# SHARED VOCABULARY
# ============================================================

LEVELS = [
    "very_high",
    "high",
    "medium",
    "low",
    "unknown",
]

CONFIDENCE_LEVELS = [
    "very_high",
    "high",
    "medium",
    "low",
    "unknown",
]


# These disciplines use the same vocabulary as Opportunity Intelligence.
# This allows the future matching engine to compare both objects directly.

CANONICAL_DISCIPLINES = [
    "classical_ballet",
    "contemporary",
    "jazz",
    "commercial",
    "hip_hop",
    "musical_theatre",
    "tap",
    "ballroom",
    "latin",
    "character",
    "folk",
    "acrobatics",
    "circus",
    "partnering",
    "improvisation",
    "singing",
    "acting",
]


# ============================================================
# HELPERS
# ============================================================

def _string_array():
    return {
        "type": "array",
        "maxItems": 8,
        "items": {
            "type": "string"
        }
    }


def _field(value_schema):
    """
    A structured field containing:
    - the extracted value
    - source evidence
    - confidence in the extraction
    """

    return {
        "type": "object",
        "properties": {
            "value": value_schema,

            "evidence": _string_array(),

            "confidence": {
                "type": "string",
                "enum": CONFIDENCE_LEVELS,
            },
        },

        "required": [
            "value",
            "evidence",
            "confidence",
        ],

        "additionalProperties": False,
    }


def _object(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


STRING = {
    "type": "string"
}


BOOL = {
    "type": "boolean"
}


# ============================================================
# DISCIPLINE
# ============================================================

DISCIPLINE_PROFILE = _object({

    "discipline": STRING,

    "level": {
        "type": "string",
        "enum": LEVELS,
    },

    "evidence": _string_array(),

    "confidence": {
        "type": "string",
        "enum": CONFIDENCE_LEVELS,
    },
})


# ============================================================
# CAPABILITY
# ============================================================

CAPABILITY_PROFILE = _object({

    "capability": STRING,

    "level": {
        "type": "string",
        "enum": LEVELS,
    },

    "evidence": _string_array(),

    "confidence": {
        "type": "string",
        "enum": CONFIDENCE_LEVELS,
    },
})


# ============================================================
# PROFESSIONAL EXPERIENCE
# ============================================================

PROFESSIONAL_EXPERIENCE = _object({

    "organisation": STRING,

    "role": STRING,

    "experience_type": STRING,

    "start_date": STRING,

    "end_date": STRING,

    "location": STRING,

    "evidence": STRING,
})


# ============================================================
# REPERTOIRE
# ============================================================

REPERTOIRE_ITEM = _object({

    "work": STRING,

    "role": STRING,

    "choreographer": STRING,

    "company": STRING,

    "year": STRING,

    "evidence": STRING,
})


# ============================================================
# TRAINING
# ============================================================

TRAINING_ITEM = _object({

    "institution": STRING,

    "programme": STRING,

    "qualification": STRING,

    "start_date": STRING,

    "end_date": STRING,

    "status": STRING,

    "institution_reputation": {
        "type": "string",
        "enum": [
            "elite_international",
            "highly_prestigious",
            "reputable",
            "standard",
            "unknown",
        ],
    },

    "evidence": STRING,
})


# ============================================================
# CHOREOGRAPHY
# ============================================================

CHOREOGRAPHY_ITEM = _object({

    "organisation": STRING,

    "role": STRING,

    "work": STRING,

    "dates": STRING,

    "evidence": STRING,
})


# ============================================================
# LANGUAGE
# ============================================================

LANGUAGE_ITEM = _object({

    "language": STRING,

    "proficiency": STRING,

    "evidence": STRING,
})


# ============================================================
# ACHIEVEMENT
# ============================================================

ACHIEVEMENT_ITEM = _object({

    "achievement": STRING,

    "result": STRING,

    "year": STRING,

    "evidence": STRING,
})


# ============================================================
# MAIN ARTIST SCHEMA
# ============================================================

ARTIST_SCHEMA = _object({

    # ========================================================
    # IDENTITY
    # ========================================================

    "identity": _object({

        "name": STRING,

        "age": STRING,

        "nationality": STRING,

        "location": STRING,
    }),


    # ========================================================
    # PHYSICAL
    # ========================================================

    "physical": _object({

        "height": STRING,

        "observable_attributes": _string_array(),

        "headshot_available": BOOL,

        "source": {
            "type": "string",
            "enum": [
                "cv",
                "headshot",
                "cv_and_headshot",
                "unknown",
            ],
        },

        "confidence": {
            "type": "string",
            "enum": CONFIDENCE_LEVELS,
        },
    }),


    # ========================================================
    # DISCIPLINES
    #
    # IMPORTANT:
    #
    # This is now a LIST instead of primary / secondary /
    # third / extras.
    #
    # This allows the AI to identify as many disciplines as
    # the artist's evidence actually supports.
    # ========================================================

    "disciplines": {
        "type": "array",
        "maxItems": 8,
        "items": DISCIPLINE_PROFILE,
    },


    # ========================================================
    # CAREER
    # ========================================================

    "career": _object({

        "current_stage": STRING,

        "current_company": STRING,

        "current_role": STRING,

        "professional_experience": {
            "type": "array",
            "maxItems": 12,
            "items": PROFESSIONAL_EXPERIENCE,
        },

        "trajectory": _string_array(),
    }),


    # ========================================================
    # REPERTOIRE
    # ========================================================

    "repertoire": {
        "type": "array",
        "maxItems": 10,
        "items": REPERTOIRE_ITEM,
    },


    # ========================================================
    # TRAINING
    # ========================================================

    "training": {
        "type": "array",
        "maxItems": 6,
        "items": TRAINING_ITEM,
    },


    # ========================================================
    # TEACHING
    # ========================================================

    "teaching": _object({

        "qualification_status": {
            "type": "string",
            "enum": [
                "yes",
                "no",
                "in_progress",
                "unknown",
            ],
        },

        "experience": _string_array(),
    }),


    # ========================================================
    # CHOREOGRAPHY
    # ========================================================

    "choreography": {
        "type": "array",
        "maxItems": 6,
        "items": CHOREOGRAPHY_ITEM,
    },


    # ========================================================
    # CAPABILITIES
    #
    # Examples:
    #
    # pas_de_deux
    # pointe
    # partnering
    # improvisation
    # acting
    # singing
    # choreography
    # teaching
    #
    # These are separate from disciplines.
    # ========================================================

    "capabilities": {
        "type": "array",
        "maxItems": 12,
        "items": CAPABILITY_PROFILE,
    },


    # ========================================================
    # LANGUAGES
    # ========================================================

    "languages": {
        "type": "array",
        "maxItems": 6,
        "items": LANGUAGE_ITEM,
    },


    # ========================================================
    # ELIGIBILITY
    # ========================================================

    "eligibility": _object({

        "work_rights": _string_array(),

        "visa_status": STRING,

        "evidence": STRING,
    }),


    # ========================================================
    # ACHIEVEMENTS
    # ========================================================

    "achievements": {
        "type": "array",
        "maxItems": 6,
        "items": ACHIEVEMENT_ITEM,
    },


    # ========================================================
    # PREFERENCES
    #
    # These are NOT used to determine technical ability.
    #
    # They represent what the artist wants.
    # ========================================================

    "preferences": _object({

        "target_roles": _string_array(),

        "preferred_disciplines": _string_array(),

        "preferred_countries": _string_array(),

        "preferred_companies": _string_array(),

        "contract_preferences": _string_array(),

        "availability": STRING,

        "salary_expectations": STRING,

        "relocation_preferences": STRING,
    }),


    # ========================================================
    # INTELLIGENCE
    #
    # This is AI-generated interpretation of the structured
    # evidence.
    #
    # The future matching engine should NOT rely on this
    # section for the numerical match score.
    #
    # The structured fields above are the source of truth.
    # ========================================================

    "intelligence": _object({

        "strengths": _string_array(),

        "evidence": _string_array(),

        "confidence": {
            "type": "string",
            "enum": CONFIDENCE_LEVELS,
        },

        "missing_information": _string_array(),

        "questions_to_ask": _string_array(),
    }),
})


# ============================================================
# EXPORT
# ============================================================

__all__ = [
    "ARTIST_SCHEMA",
    "LEVELS",
    "CONFIDENCE_LEVELS",
    "CANONICAL_DISCIPLINES",
]
