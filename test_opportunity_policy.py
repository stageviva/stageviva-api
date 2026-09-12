from __future__ import annotations

import unittest

from opportunity_policy import is_stageviva_eligible


def opportunity(*, opportunity_type: str, description: str = "", contract: str = "") -> dict:
    return {
        "identity": {
            "title": {"value": "Test opportunity"},
            "opportunity_type": {"value": opportunity_type},
            "description": {"value": description},
        },
        "contract_and_compensation": {
            "contract_type": {"value": contract}, "compensation": {"value": ""},
        },
    }


class OpportunityPolicyTest(unittest.TestCase):
    def test_excludes_school_and_training_admission(self) -> None:
        self.assertFalse(is_stageviva_eligible(opportunity(
            opportunity_type="Pre-vocational classical ballet training programme",
            description="Weekend classes and tuition for school-age dancers.",
        )))
        self.assertFalse(is_stageviva_eligible(opportunity(
            opportunity_type="Academy audition",
            description="Join our dance academy for the 2026 programme.",
        )))

    def test_excludes_an_unqualified_audition(self) -> None:
        self.assertFalse(is_stageviva_eligible(opportunity(
            opportunity_type="Audition",
            description="An opportunity for performers.",
        )))

    def test_keeps_paid_work_and_transition_roles(self) -> None:
        self.assertTrue(is_stageviva_eligible(opportunity(
            opportunity_type="Company dancer audition", description="Paid employment contract.",
        )))
        self.assertTrue(is_stageviva_eligible(opportunity(
            opportunity_type="Trainee and company positions", description="Professional company trainee role.",
        )))
