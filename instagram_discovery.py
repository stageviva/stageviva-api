"""Official Meta Instagram discovery for StageViva source accounts.

The API receives only captions and permalinks from public Professional
accounts.  It never scrapes Instagram pages, logs tokens, or reads messages.
"""

from __future__ import annotations

import os
import re
from typing import Any

import requests

from opportunity_discovery import DiscoveredOpportunity
from source_registry import Source


GRAPH_API = "https://graph.facebook.com/v24.0"
REQUEST_TIMEOUT_SECONDS = 20
OPPORTUNITY_TERMS = re.compile(
    r"\b(audition|casting|dancer|dance|singer|performer|actor|artist|"
    r"contract|vacancy|open call|recruiting|hiring|apply)\b",
    re.IGNORECASE,
)


def instagram_is_configured() -> bool:
    """Return whether Render has the two secret Meta values required to scan."""
    return bool(
        os.getenv("STAGEVIVA_INSTAGRAM_ACCESS_TOKEN", "").strip()
        and os.getenv("STAGEVIVA_INSTAGRAM_BUSINESS_ACCOUNT_ID", "").strip()
    )


def _caption_title(caption: str, handle: str) -> str:
    first_line = next((line.strip(" #–—-\t") for line in caption.splitlines() if line.strip()), "")
    cleaned = re.sub(r"\s+", " ", first_line).strip()
    return (cleaned[:117].rstrip() + "...") if len(cleaned) > 120 else (cleaned or f"Instagram post from @{handle}")


def _media_for_handle(handle: str) -> list[dict[str, Any]]:
    token = os.environ["STAGEVIVA_INSTAGRAM_ACCESS_TOKEN"].strip()
    account_id = os.environ["STAGEVIVA_INSTAGRAM_BUSINESS_ACCOUNT_ID"].strip()
    fields = (
        f"business_discovery.username({handle})"
        "{media.limit(25){id,caption,permalink,timestamp,media_type}}"
    )
    response = requests.get(
        f"{GRAPH_API}/{account_id}",
        params={"fields": fields, "access_token": token},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    discovery = payload.get("business_discovery") or {}
    media = discovery.get("media") or {}
    return [item for item in media.get("data", []) if isinstance(item, dict)]


def discover_instagram_source(source: Source) -> list[DiscoveredOpportunity]:
    """Turn relevant public post captions into conservative pipeline inputs."""
    if source.source_type != "instagram" or not source.instagram_handle:
        raise ValueError("Instagram discovery requires an Instagram source handle.")
    if not instagram_is_configured():
        return []

    results: list[DiscoveredOpportunity] = []
    for media in _media_for_handle(source.instagram_handle):
        caption = str(media.get("caption") or "").strip()
        permalink = str(media.get("permalink") or "").strip()
        if not caption or not permalink or not OPPORTUNITY_TERMS.search(caption):
            continue
        description = (
            f"Instagram source: @{source.instagram_handle}\n"
            f"Post permalink: {permalink}\n"
            f"Published: {media.get('timestamp') or 'unknown'}\n\n"
            f"Caption:\n{caption}"
        )
        results.append(DiscoveredOpportunity(
            title=_caption_title(caption, source.instagram_handle),
            listing_url=permalink,
            source_name=source.name,
            source_url=source.url,
            category=source.category,
            description=description,
        ))
    return results
