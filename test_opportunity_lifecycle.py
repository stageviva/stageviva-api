from datetime import date
import unittest

from opportunity_lifecycle import is_current_opportunity


class OpportunityLifecycleTest(unittest.TestCase):
    def test_rejects_when_all_explicit_dates_are_past(self) -> None:
        opportunity = {"dates": {"application_deadline": {"value": "1 September 2026"}, "audition_dates": {"value": ["2 September 2026"]}}}
        self.assertFalse(is_current_opportunity(opportunity, today=date(2026, 9, 3)))

    def test_keeps_opportunity_when_one_date_is_still_open_or_dates_unknown(self) -> None:
        future = {"dates": {"application_deadline": {"value": "5 September 2026"}, "audition_dates": {"value": []}}}
        self.assertTrue(is_current_opportunity(future, today=date(2026, 9, 3)))
        self.assertTrue(is_current_opportunity({}, today=date(2026, 9, 3)))


if __name__ == "__main__":
    unittest.main()
