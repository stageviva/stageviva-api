from dataclasses import dataclass
import hashlib
import re
from typing import List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


# ============================================================
# SOURCE DEFINITION
# ============================================================

@dataclass
class DiscoverySource:
    name: str
    url: str
    category: str
    priority: int = 1


# ============================================================
# DISCOVERY RESULT
# ============================================================

@dataclass
class DiscoveredOpportunity:
    title: str
    listing_url: str
    source_name: str
    source_url: str
    category: str

    organisation: Optional[str] = None
    deadline: Optional[str] = None
    location: Optional[str] = None

    description: Optional[str] = None


# ============================================================
# HTTP
# ============================================================

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/151.0 Safari/537.36"
    )
}


def fetch_page(url: str) -> Optional[str]:
    """
    Download a public webpage.

    Returns HTML or None if the page cannot be retrieved.
    """

    try:
        response = requests.get(
            url,
            headers=DEFAULT_HEADERS,
            timeout=20,
        )

        response.raise_for_status()

        return response.text

    except requests.RequestException as error:
        print(f"[ERROR] Could not fetch {url}: {error}")
        return None


# ============================================================
# BASIC LINK DISCOVERY
# ============================================================

def extract_links(
    html: str,
    source: DiscoverySource,
) -> List[DiscoveredOpportunity]:
    """
    Extract candidate opportunity links from a source page.

    This is intentionally conservative.

    It does NOT use AI.
    It does NOT guess whether something is an audition.
    It simply collects likely listing links.

    Opportunity Intelligence will analyse the actual listing later.
    """

    soup = BeautifulSoup(html, "html.parser")

    results = []

    for link in soup.find_all("a", href=True):

        href = link.get("href")
        title = link.get_text(" ", strip=True)

        if not href or not title:
            continue

        listing_url = urljoin(source.url, href)

        text = title.lower()

        keywords = [
            "audition",
            "casting",
            "dance",
            "dancer",
            "performer",
            "actor",
            "musical",
            "cruise",
            "entertainment",
            "job",
            "vacancy",
        ]

        if not any(keyword in text for keyword in keywords):
            continue

        results.append(
            DiscoveredOpportunity(
                title=title,
                listing_url=listing_url,
                source_name=source.name,
                source_url=source.url,
                category=source.category,
            )
        )

    return results


# ============================================================
# DUPLICATE DETECTION
# ============================================================

def normalise_url(url: str) -> str:
    """
    Basic URL normalisation.
    """

    return url.rstrip("/").lower()


def deduplicate_opportunities(
    opportunities: List[DiscoveredOpportunity],
) -> List[DiscoveredOpportunity]:

    seen_urls = set()
    unique = []

    for opportunity in opportunities:

        key = normalise_url(opportunity.listing_url)

        if key in seen_urls:
            continue

        seen_urls.add(key)
        unique.append(opportunity)

    return unique


# ============================================================
# SOURCE DISCOVERY
# ============================================================

def discover_from_source(
    source: DiscoverySource,
) -> List[DiscoveredOpportunity]:

    print(f"\n[DISCOVERY] {source.name}")

    html = fetch_page(source.url)

    if not html:
        return []

    opportunities = extract_links(
        html=html,
        source=source,
    )

    print(
        f"[FOUND] {len(opportunities)} candidate listings "
        f"from {source.name}"
    )

    return opportunities


# ============================================================
# BALLETPLACES ADAPTER
# ============================================================

def discover_balletplaces() -> List[DiscoveredOpportunity]:
    """
    Discover current ballet auditions from BalletPlaces.
    """

    source = DiscoverySource(
        name="BalletPlaces",
        url="https://balletplaces.com/ballet-auditions/accepting-applications/",
        category="ballet_dance",
        priority=1,
    )

    print(f"\n[DISCOVERY] {source.name}")

    html = fetch_page(source.url)

    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")

    results = []

    for link in soup.find_all("a", href=True):

        title = link.get_text(" ", strip=True)
        href = link.get("href")

        if not title or not href:
            continue

        title_lower = title.lower()

        if "audition" not in title_lower:
            continue

        listing_url = urljoin(source.url, href)

        # BalletPlaces navigation contains index pages too. Only individual
        # /auditions/<listing> pages are valid candidates for intelligence.
        listing_path = urlparse(listing_url).path.rstrip("/")
        if not listing_path.startswith("/auditions/"):
            continue

        results.append(
            DiscoveredOpportunity(
                title=title,
                listing_url=listing_url,
                source_name="BalletPlaces",
                source_url=source.url,
                category="ballet_dance",
            )
        )

    results = deduplicate_opportunities(results)

    print(
        f"[FOUND] {len(results)} candidate listings "
        f"from BalletPlaces"
    )

    return results


def discover_dance_europe() -> List[DiscoveredOpportunity]:
    """Discover Dance Europe notices, including notices that use email only."""
    source = DiscoverySource(
        name="Dance Europe",
        url="https://danceeurope.net/auditions/",
        category="ballet_dance",
        priority=1,
    )
    print(f"\n[DISCOVERY] {source.name}")
    html = fetch_page(source.url)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    results: List[DiscoveredOpportunity] = []
    for block in soup.select(".rich-text"):
        heading = block.find(["h2", "h3", "h4"])
        title = heading.get_text(" ", strip=True) if heading else ""
        description = block.get_text(" ", strip=True)
        if not title or len(description) < 60:
            continue
        lower = f"{title} {description}".lower()
        if not any(word in lower for word in ["audition", "dancer", "dance", "ballet", "performer", "casting"]):
            continue
        official_links = [
            urljoin(source.url, link["href"])
            for link in block.find_all("a", href=True)
            if link["href"].startswith(("http://", "https://"))
        ]
        listing_url = official_links[0] if official_links else (
            f"{source.url}#notice-{hashlib.sha256(description.encode('utf-8')).hexdigest()[:16]}"
        )
        results.append(DiscoveredOpportunity(
            title=title,
            listing_url=listing_url,
            source_name=source.name,
            source_url=source.url,
            category=source.category,
            description=description,
        ))
    results = deduplicate_opportunities(results)
    print(f"[FOUND] {len(results)} candidate listings from {source.name}")
    return results


def discover_ballee() -> List[DiscoveredOpportunity]:
    """Discover individual audition pages listed publicly by Ballee."""
    source = DiscoverySource("Ballee", "https://ballee.co/auditions", "ballet_dance", 2)
    print(f"\n[DISCOVERY] {source.name}")
    html = fetch_page(source.url)
    if not html:
        return []
    results: List[DiscoveredOpportunity] = []
    soup = BeautifulSoup(html, "html.parser")
    for link in soup.find_all("a", href=True):
        title = link.get_text(" ", strip=True)
        listing_url = urljoin(source.url, link["href"])
        path = urlparse(listing_url).path.rstrip("/")
        if not title or not path.startswith("/auditions/") or path.startswith("/auditions/companies/"):
            continue
        results.append(DiscoveredOpportunity(
            title=title,
            listing_url=listing_url,
            source_name=source.name,
            source_url=source.url,
            category=source.category,
        ))
    results = deduplicate_opportunities(results)
    print(f"[FOUND] {len(results)} candidate listings from {source.name}")
    return results


def discover_entertainers_worldwide() -> List[DiscoveredOpportunity]:
    """Discover public performer opportunities from Entertainers Worldwide."""
    source = DiscoverySource(
        "Entertainers Worldwide", "https://www.entertainersworldwidejobs.com/", "performing_arts", 1,
    )
    performer_categories = {
        "dancer-jobs", "singer-jobs", "musician-jobs", "actor-extra-model-voice-over-jobs",
        "speciality-variety-act-jobs", "childrens-entertainer-jobs", "comedy-act-jobs",
    }
    print(f"\n[DISCOVERY] {source.name}")
    html = fetch_page(source.url)
    if not html:
        return []
    results: List[DiscoveredOpportunity] = []
    for link in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        title = link.get_text(" ", strip=True)
        listing_url = urljoin(source.url, link["href"])
        parts = [part for part in urlparse(listing_url).path.split("/") if part]
        if len(parts) != 2 or parts[0] not in performer_categories or not title:
            continue
        if parts[1] in {"apply", ""} or not parts[1].rsplit("-", 1)[-1].isdigit():
            continue
        results.append(DiscoveredOpportunity(
            title=title, listing_url=listing_url, source_name=source.name,
            source_url=source.url, category=source.category,
        ))
    results = deduplicate_opportunities(results)
    print(f"[FOUND] {len(results)} candidate listings from {source.name}")
    return results


def discover_allcasting() -> List[DiscoveredOpportunity]:
    """Discover publicly featured casting calls from AllCasting."""
    source = DiscoverySource("AllCasting", "https://allcasting.com/", "acting", 2)
    print(f"\n[DISCOVERY] {source.name}")
    html = fetch_page(source.url)
    if not html:
        return []
    results: List[DiscoveredOpportunity] = []
    for link in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        title = link.get_text(" ", strip=True)
        listing_url = urljoin(source.url, link["href"])
        if not title or not re.fullmatch(r"/castingcall/\d+", urlparse(listing_url).path):
            continue
        results.append(DiscoveredOpportunity(
            title=title, listing_url=listing_url, source_name=source.name,
            source_url=source.url, category=source.category,
        ))
    results = deduplicate_opportunities(results)
    print(f"[FOUND] {len(results)} candidate listings from {source.name}")
    return results


# ============================================================
# MAIN DISCOVERY PIPELINE
# ============================================================

def discover_opportunities(
    sources: List[DiscoverySource],
) -> List[DiscoveredOpportunity]:

    all_opportunities = []

    for source in sources:

        opportunities = discover_from_source(source)

        all_opportunities.extend(opportunities)

    unique_opportunities = deduplicate_opportunities(
        all_opportunities
    )

    print(
        f"\n[TOTAL] {len(unique_opportunities)} unique "
        f"candidate opportunities"
    )

    return unique_opportunities


# ============================================================
# TEST
# ============================================================

if __name__ == "__main__":

    print("StageViva Opportunity Discovery Engine")
    print("--------------------------------------")

    opportunities = discover_balletplaces()

    print(
        f"\nFound {len(opportunities)} candidate opportunities."
    )

    for opportunity in opportunities:

        print("\n----------------------------")

        print(f"Title: {opportunity.title}")
        print(f"URL: {opportunity.listing_url}")
        print(f"Source: {opportunity.source_name}")
        print(f"Category: {opportunity.category}")
