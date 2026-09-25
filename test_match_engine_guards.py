from __future__ import annotations

import unittest

from match_engine import (
    _apply_gender_requirement_guard,
    _apply_location_and_work_rights_guard,
    _apply_requirement_guards,
    _calibrate_evidence_score,
)


class MatchRequirementGuardsTest(unittest.TestCase):
    def test_missing_high_singing_caps_a_dance_match(self) -> None:
        artist = {"disciplines": [{"discipline": "classical_ballet", "level": "high"}]}
        opportunity = {
            "requirements": {
                "discipline_requirements": {
                    "singing": {"required_level": "high"},
                },
            },
        }
        result = {
            "overall": {"match_score": 82, "match_level": "strong", "recommendation": "good_match"},
            "gaps": [],
        }
        guarded = _apply_requirement_guards(artist, opportunity, result)
        self.assertEqual(guarded["overall"]["match_score"], 45)
        self.assertEqual(guarded["overall"]["match_level"], "weak")
        self.assertEqual(guarded["overall"]["recommendation"], "weak_match")

    def test_known_required_skill_does_not_change_score(self) -> None:
        artist = {"disciplines": [{"discipline": "singing", "level": "high"}]}
        opportunity = {
            "requirements": {
                "discipline_requirements": {
                    "singing": {"required_level": "high"},
                },
            },
        }
        result = {"overall": {"match_score": 82}, "gaps": []}
        self.assertEqual(_apply_requirement_guards(artist, opportunity, result)["overall"]["match_score"], 82)

    def test_missing_high_ballet_requirement_cannot_be_a_strong_match(self) -> None:
        artist = {"disciplines": [{"discipline": "musical_theatre", "level": "high"}]}
        opportunity = {
            "requirements": {
                "discipline_requirements": {
                    "classical_ballet": {"required_level": "high"},
                },
            },
        }
        result = {"overall": {"match_score": 91}, "gaps": []}
        guarded = _apply_requirement_guards(artist, opportunity, result)
        self.assertEqual(guarded["overall"]["match_score"], 55)

    def test_evidence_calibration_breaks_coarse_score_ties(self) -> None:
        result = {
            "overall": {"match_score": 85},
            "match_reasons": [{}, {}, {}],
            "strengths": [{}, {}],
            "gaps": [{}],
        }
        self.assertEqual(_calibrate_evidence_score(result)["overall"]["match_score"], 90)

    def test_explicit_gender_conflict_is_capped_at_twenty_percent(self) -> None:
        artist = {"identity": {"gender": "female"}}
        opportunity = {"requirements": {"physical": {"gender_requirement": {"value": "Male dancer required"}}}}
        result = {"overall": {"match_score": 87}, "gaps": [], "physical_match": {}}
        guarded = _apply_gender_requirement_guard(artist, opportunity, result)
        self.assertEqual(guarded["overall"]["match_score"], 20)
        self.assertEqual(guarded["overall"]["recommendation"], "not_recommended")
        self.assertEqual(guarded["physical_match"]["gender"]["status"], "ineligible")

    def test_mixed_gender_listing_does_not_penalise_anyone(self) -> None:
        artist = {"identity": {"gender": "female"}}
        opportunity = {"requirements": {"physical": {"gender_requirement": {"value": "Male & Female acrobatic duos"}}}}
        result = {"overall": {"match_score": 87}, "gaps": []}
        self.assertEqual(_apply_gender_requirement_guard(artist, opportunity, result)["overall"]["match_score"], 87)

    def test_unconfirmed_us_work_rights_cap_an_otherwise_strong_match(self) -> None:
        artist = {
            "eligibility": {"work_rights": ["United Kingdom", "European Union"]},
            "preferences": {"preferred_locations": ["United Kingdom", "Europe"]},
        }
        opportunity = {
            "location": {"city": {"value": "New York"}, "country": {"value": "United States"}},
            "requirements": {"work_rights_or_visa": {"value": "Not specified"}},
            "contract_and_compensation": {"visa_support": {"value": "Not specified"}},
        }
        result = {"overall": {"match_score": 91, "recommendation": "strong_match"}, "gaps": []}
        guarded = _apply_location_and_work_rights_guard(artist, opportunity, result)
        self.assertEqual(guarded["overall"]["match_score"], 40)
        self.assertEqual(guarded["overall"]["recommendation"], "weak_match")
        self.assertEqual(guarded["eligibility"]["status"], "possible_issue")

    def test_stated_visa_support_keeps_an_overseas_opportunity_possible(self) -> None:
        artist = {"eligibility": {"work_rights": ["United Kingdom", "European Union"]}}
        opportunity = {
            "location": {"country": {"value": "United States"}},
            "contract_and_compensation": {"visa_support": {"value": "Visa sponsorship provided"}},
        }
        result = {"overall": {"match_score": 91, "recommendation": "strong_match"}, "gaps": []}
        guarded = _apply_location_and_work_rights_guard(artist, opportunity, result)
        self.assertEqual(guarded["overall"]["match_score"], 70)
        self.assertEqual(guarded["overall"]["recommendation"], "possible_match")


if __name__ == "__main__":
    unittest.main()
