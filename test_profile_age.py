from __future__ import annotations

from datetime import date
import unittest

from api import _age_from_date_of_birth, _apply_profile_answers_locally, _synchronise_date_of_birth


class ProfileAgeTest(unittest.TestCase):
    def test_age_is_calculated_from_date_of_birth(self) -> None:
        self.assertEqual(_age_from_date_of_birth("2003-09-07", date(2026, 9, 6)), "22")
        self.assertEqual(_age_from_date_of_birth("2003-09-06", date(2026, 9, 6)), "23")

    def test_date_of_birth_answer_is_saved_without_ai(self) -> None:
        artist, applied = _apply_profile_answers_locally(
            {"identity": {"age": "19"}},
            [{"id": "date_of_birth", "answer": "2003-01-15"}],
        )
        self.assertEqual(applied, 1)
        self.assertEqual(artist["identity"]["date_of_birth"], "2003-01-15")
        self.assertEqual(artist["identity"]["age"], str(date.today().year - 2003 - (date.today() < date(date.today().year, 1, 15))))

    def test_profile_edit_cannot_leave_a_stale_age(self) -> None:
        identity = {"age": "19", "date_of_birth": "2003-01-15"}
        _synchronise_date_of_birth(identity)
        self.assertNotEqual(identity["age"], "19")


if __name__ == "__main__":
    unittest.main()
