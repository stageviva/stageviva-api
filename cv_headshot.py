"""Extract a performer-approved headshot embedded in an uploaded PDF CV.

CVs frequently contain logos, show artwork and social-media icons as well as a
headshot. We therefore keep extraction deliberately conservative: image
candidates are selected by size, then a low-cost vision pass must identify a
clear single-person portrait before anything is saved.
"""

from __future__ import annotations

import base64
import io
import os
import re
from pathlib import Path
from typing import Any

from openai import OpenAI
from pypdf import PdfReader


HEADSHOT_MODEL = os.getenv("STAGEVIVA_HEADSHOT_MODEL", "gpt-4o-mini")
MAX_CANDIDATES = 6


def _headshot_choice(response_text: str, candidate_count: int) -> int | None:
    """Read the intentionally tiny vision response without trusting prose."""
    matched = re.search(r"HEADSHOT\s*:\s*(\d+)", str(response_text or ""), re.IGNORECASE)
    if not matched:
        return None
    index = int(matched.group(1))
    return index if 1 <= index <= candidate_count else None


def _candidate_images(cv_path: str | Path) -> list[Any]:
    """Return only reasonably-sized, displayed PDF images likely to be photos."""
    if Path(cv_path).suffix.lower() != ".pdf":
        return []
    reader = PdfReader(str(cv_path))
    candidates: list[Any] = []
    for page in reader.pages:
        try:
            for item in page.images:
                if not item.is_displayed or item.image is None:
                    continue
                image = item.image
                width, height = image.size
                if min(width, height) < 120 or width * height < 25_000:
                    continue
                aspect_ratio = width / height if height else 0
                if not 0.45 <= aspect_ratio <= 1.8:
                    continue
                candidates.append(image.copy())
        except Exception:
            # A malformed image object must never fail the CV analysis itself.
            continue
    return sorted(candidates, key=lambda image: image.size[0] * image.size[1], reverse=True)[:MAX_CANDIDATES]


def _choose_headshot(candidates: list[Any], client: OpenAI) -> Any | None:
    if not candidates:
        return None
    content: list[dict[str, Any]] = [{
        "type": "input_text",
        "text": (
            "These are images embedded in one performer's CV, numbered in order. "
            "Choose an image ONLY if it is a clear, professional-looking headshot or "
            "upper-body portrait of one real person. Do not select a logo, poster, "
            "production image, group photograph, illustration, or decorative graphic. "
            "Do not identify the person or infer personal attributes. "
            "Reply exactly HEADSHOT: N, or NONE."
        ),
    }]
    for index, image in enumerate(candidates, start=1):
        encoded = io.BytesIO()
        image.convert("RGB").save(encoded, format="JPEG", quality=82)
        content.append({"type": "input_text", "text": f"Candidate {index}"})
        content.append({
            "type": "input_image",
            "image_url": f"data:image/jpeg;base64,{base64.b64encode(encoded.getvalue()).decode('ascii')}",
            "detail": "low",
        })
    response = client.responses.create(
        model=HEADSHOT_MODEL,
        max_output_tokens=30,
        input=[{"role": "user", "content": content}],
    )
    choice = _headshot_choice(response.output_text, len(candidates))
    return candidates[choice - 1] if choice is not None else None


def extract_cv_headshot(cv_path: str | Path, destination: str | Path, client: OpenAI | None = None) -> bool:
    """Save a confirmed headshot as JPEG and return whether a new one was found.

    Failure is non-fatal: a CV without a headshot remains usable, and an
    existing saved headshot is never removed by this function.
    """
    try:
        portrait = _choose_headshot(_candidate_images(cv_path), client or OpenAI())
        if portrait is None:
            return False
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        portrait.convert("RGB").save(temporary, format="JPEG", quality=90, optimize=True)
        temporary.replace(target)
        return True
    except Exception:
        return False
