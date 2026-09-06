from __future__ import annotations

from typing import Any

from match_engine import match_artist_to_opportunity as _calculate_match


def match_artist_to_opportunity(
    artist_dna: dict[str, Any],
    opportunity: dict[str, Any],
) -> dict[str, Any]:
    """
    Connects Artist Intelligence with Opportunity Intelligence.

    Artist DNA and Opportunity Intelligence remain separate.
    The Match Engine is responsible only for comparing them.
    """

    return _calculate_match(
        artist_dna=artist_dna,
        opportunity=opportunity,
    )
