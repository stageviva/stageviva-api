"""Read every screenshot region locally within one bounded OCR time budget."""

import time
from pathlib import Path

from PIL import Image, ImageOps, ImageStat

from artist_intelligence import _extract_image_text as _primary_reader


def screenshot_regions(width: int, height: int) -> list[tuple[int, int, int, int]]:
    # Three overlapping bands include the bottom even on very tall captions.
    step = (height + 2) // 3
    overlap = min(180, step // 5)
    return [(0, max(0, top - overlap), width, min(height, top + step + overlap))
            for top in range(0, height, step)]


def read_screenshot(path: Path, max_characters: int, *, timeout: int = 20) -> str:
    import pytesseract

    deadline = time.monotonic() + (timeout or 20)
    parts = []
    primary_at_limit = False
    try:
        primary = _primary_reader(path, max_characters, timeout=max(1, min(7, timeout or 7)))
        primary_at_limit = len(primary) >= max_characters
        parts.append(primary)
    except Exception:
        # The band passes may still read a screenshot with mixed photo/text.
        pass
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("L")
        for region in screenshot_regions(*image.size):
            remaining = deadline - time.monotonic()
            if remaining < 1:
                break
            band = image.crop(region)
            if ImageStat.Stat(band).median[0] < 128:
                band = ImageOps.invert(band)
            band = ImageOps.autocontrast(band)
            try:
                text = pytesseract.image_to_string(band, lang="eng", config="--psm 6", timeout=max(1, min(4, int(remaining))))
                if text.strip():
                    parts.append(text.strip())
            except Exception:
                continue
    # Preserve reading order inside each pass. Repeated lines from overlap need
    # not enlarge the prompt or make the same deadline look like two facts.
    seen, lines = set(), []
    for part in parts:
        for line in part.splitlines():
            line = line.strip()
            key = " ".join(line.casefold().split())
            if key and key not in seen:
                lines.append(line)
                seen.add(key)
    if not lines:
        raise ValueError("No readable screenshot text")
    result = "\n".join(lines)
    if primary_at_limit or len(result) > max_characters:
        raise ValueError("Screenshot text exceeds the supported input size")
    return result
