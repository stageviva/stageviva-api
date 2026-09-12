from __future__ import annotations

import unittest

from cv_headshot import _headshot_choice


class HeadshotChoiceTest(unittest.TestCase):
    def test_accepts_only_an_in_range_explicit_choice(self) -> None:
        self.assertEqual(_headshot_choice("HEADSHOT: 2", 3), 2)
        self.assertIsNone(_headshot_choice("HEADSHOT: 4", 3))
        self.assertIsNone(_headshot_choice("NONE", 3))


if __name__ == "__main__":
    unittest.main()
