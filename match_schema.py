"""Strict Structured Outputs schema for StageViva Matching Engine."""

LEVELS = [
    "very_high",
    "high",
    "medium",
    "low",
    "unknown",
]

MATCH_LEVELS = [
    "excellent",
    "strong",
    "moderate",
    "weak",
    "very_weak",
    "unknown",
]

ELIGIBILITY_STATUS = [
    "eligible",
    "possible_issue",
    "ineligible",
    "unknown",
]

RECOMMENDATION_LEVELS = [
    "strong_match",
    "good_match",
    "possible_match",
    "weak_match",
    "not_recommended",
    "unknown",
]


def _string_array():
    return {
        "type": "array",
        "items": {"type": "string"},
    }


def _object(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


MATCH_SCHEMA = _object({

    # ============================================================
    # OVERALL MATCH
    # ============================================================

    "overall": _object({

        "match_score": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
        },

        "match_level": {
            "type": "string",
            "enum": MATCH_LEVELS,
        },

        "recommendation": {
            "type": "string",
            "enum": RECOMMENDATION_LEVELS,
        },

        "summary": {
            "type": "string",
        },

        "confidence": {
            "type": "string",
            "enum": LEVELS,
        },
    }),

    # ============================================================
    # DISCIPLINE MATCH
    # ============================================================

    "discipline_match": {
        "type": "array",
        "items": _object({

            "discipline": {
                "type": "string",
            },

            "artist_level": {
                "type": "string",
                "enum": LEVELS,
            },

            "opportunity_requirement": {
                "type": "string",
                "enum": LEVELS,
            },

            "compatibility": {
                "type": "string",
                "enum": MATCH_LEVELS,
            },

            "evidence": _string_array(),

            "impact": {
                "type": "string",
                "enum": [
                    "major_positive",
                    "positive",
                    "neutral",
                    "negative",
                    "major_negative",
                    "unknown",
                ],
            },
        }),
    },

    # ============================================================
    # EXPERIENCE MATCH
    # ============================================================

    "experience_match": _object({

        "professional_experience": _object({
            "status": {
                "type": "string",
                "enum": MATCH_LEVELS,
            },

            "evidence": _string_array(),

            "impact": {
                "type": "string",
                "enum": [
                    "major_positive",
                    "positive",
                    "neutral",
                    "negative",
                    "major_negative",
                    "unknown",
                ],
            },
        }),

        "repertoire": _object({
            "status": {
                "type": "string",
                "enum": MATCH_LEVELS,
            },

            "evidence": _string_array(),

            "impact": {
                "type": "string",
                "enum": [
                    "major_positive",
                    "positive",
                    "neutral",
                    "negative",
                    "major_negative",
                    "unknown",
                ],
            },
        }),

        "training": _object({
            "status": {
                "type": "string",
                "enum": MATCH_LEVELS,
            },

            "evidence": _string_array(),

            "impact": {
                "type": "string",
                "enum": [
                    "major_positive",
                    "positive",
                    "neutral",
                    "negative",
                    "major_negative",
                    "unknown",
                ],
            },
        }),

        "special_capabilities": _object({
            "status": {
                "type": "string",
                "enum": MATCH_LEVELS,
            },

            "evidence": _string_array(),

            "impact": {
                "type": "string",
                "enum": [
                    "major_positive",
                    "positive",
                    "neutral",
                    "negative",
                    "major_negative",
                    "unknown",
                ],
            },
        }),
    }),

    # ============================================================
    # PHYSICAL REQUIREMENTS
    # ============================================================

    "physical_match": _object({

        "age": _object({
            "status": {
                "type": "string",
                "enum": ELIGIBILITY_STATUS,
            },

            "evidence": _string_array(),
        }),

        "height": _object({
            "status": {
                "type": "string",
                "enum": ELIGIBILITY_STATUS,
            },

            "evidence": _string_array(),
        }),

        "gender": _object({
            "status": {
                "type": "string",
                "enum": ELIGIBILITY_STATUS,
            },

            "evidence": _string_array(),
        }),

        "other_requirements": _object({
            "status": {
                "type": "string",
                "enum": MATCH_LEVELS,
            },

            "evidence": _string_array(),
        }),
    }),

    # ============================================================
    # ELIGIBILITY
    # ============================================================

    "eligibility": _object({

        "status": {
            "type": "string",
            "enum": ELIGIBILITY_STATUS,
        },

        "work_rights": {
            "type": "string",
        },

        "visa": {
            "type": "string",
        },

        "languages": {
            "type": "string",
        },

        "evidence": _string_array(),
    }),

    # ============================================================
    # DATES
    # ============================================================

    "dates": _object({

        "status": {
            "type": "string",
            "enum": [
                "compatible",
                "potential_conflict",
                "conflict",
                "unknown",
            ],
        },

        "evidence": _string_array(),

        "impact": {
            "type": "string",
            "enum": [
                "major_positive",
                "positive",
                "neutral",
                "negative",
                "major_negative",
                "unknown",
            ],
        },
    }),

    # ============================================================
    # PREFERENCES
    # ============================================================

    "preference_match": _object({

        "disciplines": {
            "type": "string",
            "enum": MATCH_LEVELS,
        },

        "roles": {
            "type": "string",
            "enum": MATCH_LEVELS,
        },

        "countries": {
            "type": "string",
            "enum": MATCH_LEVELS,
        },

        "companies": {
            "type": "string",
            "enum": MATCH_LEVELS,
        },

        "contract": {
            "type": "string",
            "enum": MATCH_LEVELS,
        },

        "salary": {
            "type": "string",
            "enum": MATCH_LEVELS,
        },

        "relocation": {
            "type": "string",
            "enum": MATCH_LEVELS,
        },

        "evidence": _string_array(),
    }),

    # ============================================================
    # STRENGTHS
    # ============================================================

    "strengths": {
        "type": "array",
        "items": _object({

            "factor": {
                "type": "string",
            },

            "description": {
                "type": "string",
            },

            "evidence": _string_array(),

            "impact": {
                "type": "string",
                "enum": [
                    "major_positive",
                    "positive",
                ],
            },
        }),
    },

    # ============================================================
    # GAPS / RISKS
    # ============================================================

    "gaps": {
        "type": "array",
        "items": _object({

            "factor": {
                "type": "string",
            },

            "description": {
                "type": "string",
            },

            "evidence": _string_array(),

            "severity": {
                "type": "string",
                "enum": [
                    "major",
                    "moderate",
                    "minor",
                    "unknown",
                ],
            },
        }),
    },

    # ============================================================
    # MATCH EXPLANATION
    # ============================================================

    "match_reasons": {
        "type": "array",
        "items": _object({

            "reason": {
                "type": "string",
            },

            "evidence": _string_array(),

            "importance": {
                "type": "string",
                "enum": [
                    "major",
                    "moderate",
                    "minor",
                ],
            },
        }),
    },

    # ============================================================
    # USER-FACING NOTIFICATION
    # ============================================================

    "notification": _object({

        "should_notify": {
            "type": "boolean",
        },

        "headline": {
            "type": "string",
        },

        "message": {
            "type": "string",
        },

        "match_score": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
        },

        "top_reasons": _string_array(),
    }),

    # ============================================================
    # MISSING INFORMATION
    # ============================================================

    "missing_information": _string_array(),

    # ============================================================
    # OVERALL CONFIDENCE
    # ============================================================

    "overall_confidence": {
        "type": "string",
        "enum": LEVELS,
    },
})