from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from artist_intelligence import (
    _cap_training_only_classical_ballet,
    _material_credit_lines,
    _normalise_artist_disciplines,
    extract_cv_text,
)


class ArtistDisciplineNormalisationTest(unittest.TestCase):
    def test_shared_matching_vocabulary_is_used(self) -> None:
        artist = {
            "disciplines": [
                {"discipline": "ballet"},
                {"discipline": "musical theatre"},
                {"discipline": "hip hop"},
            ],
        }
        result = _normalise_artist_disciplines(artist)
        self.assertEqual(
            [item["discipline"] for item in result["disciplines"]],
            ["classical_ballet", "musical_theatre", "hip_hop"],
        )

    def test_ballet_training_without_a_ballet_career_is_not_high(self) -> None:
        artist = {
            "disciplines": [{"discipline": "classical_ballet", "level": "high", "confidence": "high"}],
            "career": {"current_role": "Singer-dancer", "professional_experience": []},
            "repertoire": [],
        }
        result = _cap_training_only_classical_ballet(artist)
        self.assertEqual(result["disciplines"][0]["level"], "medium")

    def test_professional_ballet_company_career_can_remain_high(self) -> None:
        artist = {
            "disciplines": [{"discipline": "classical_ballet", "level": "high", "confidence": "high"}],
            "career": {"current_company": "Ballet Cymru", "current_role": "Company dancer"},
            "repertoire": [],
        }
        result = _cap_training_only_classical_ballet(artist)
        self.assertEqual(result["disciplines"][0]["level"], "high")

    def test_material_credit_lines_include_named_understudy_credit(self) -> None:
        cv = (
            "2026, Ensemble, Understudy Flo, Cover Veronica, SATURDAY NIGHT FEVER, "
            "Royal Caribbean Entertainment\n"
            "Ballet training 2024\n"
        )
        lines = _material_credit_lines(cv)
        self.assertEqual(len(lines), 1)
        self.assertIn("Royal Caribbean Entertainment", lines[0])

    def test_image_only_pdf_uses_local_ocr_fallback(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "cv.pdf"
            path.write_bytes(b"placeholder")
            empty_page = unittest.mock.Mock()
            empty_page.extract_text.return_value = ""
            reader = unittest.mock.Mock(pages=[empty_page])
            with patch("pypdf.PdfReader", return_value=reader), patch(
                "artist_intelligence._extract_scanned_pdf_text",
                return_value="BCCA FINLAY\nProfessional dancer",
            ) as ocr:
                text = extract_cv_text(str(path))
            self.assertIn("Professional dancer", text)
            ocr.assert_called_once_with(path, 45_000)


if __name__ == "__main__":
    unittest.main()
