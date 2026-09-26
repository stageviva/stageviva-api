from dataclasses import dataclass
from typing import List


@dataclass
class Source:
    name: str
    url: str
    category: str
    priority: int = 1
    active: bool = True
    automation_ready: bool = False
    # A small number of employers maintain one official application page and
    # update the roles on that page.  Those sources are safe to re-analyse on
    # later scans; ordinary listing boards remain URL-deduplicated.
    refresh_existing: bool = False
    # Instagram sources are queried through Meta's official API.  The handle
    # is kept separately so the public source URL remains useful to humans.
    source_type: str = "web"
    instagram_handle: str = ""
    # A newly connected social source can expose a large backlog. Keep the
    # first daily pass deliberately small; URL deduplication advances to the
    # next unseen post on later runs.
    per_run_limit: int | None = None


# ============================================================
# STAGEVIVA SOURCE REGISTRY v0.1
#
# These are DISCOVERY sources.
#
# StageViva should first discover an opportunity here.
# It should then attempt to find and verify the official
# source before creating the final Opportunity object.
# ============================================================

SOURCES: List[Source] = [

    # ========================================================
    # BALLET / DANCE
    # ========================================================

    Source(
        name="BalletPlaces",
        url="https://balletplaces.com/auditions/",
        category="ballet_dance",
        priority=1,
        automation_ready=True,
    ),

    Source(
        name="Dance Europe",
        url="https://danceeurope.net/auditions/",
        category="ballet_dance",
        priority=1,
        automation_ready=True,
    ),

    Source(
        name="Open Auditions UK",
        url="https://www.openauditions.uk/",
        category="ballet_dance",
        priority=1,
    ),

    # These can be activated after we build/test their
    # individual source adapters.
    Source(
        name="Audition Dance",
        url="https://audition.dance/",
        category="ballet_dance",
        priority=2,
    ),

    Source(
        name="Ballee",
        url="https://ballee.co/auditions",
        category="ballet_dance",
        priority=2,
        automation_ready=True,
    ),


    # ========================================================
    # GENERAL PERFORMING ARTS / ENTERTAINMENT
    # ========================================================

    Source(
        name="Entertainers Worldwide",
        url="https://www.entertainersworldwidejobs.com/",
        category="performing_arts",
        priority=1,
        automation_ready=True,
    ),

    Source(
        name="Backstage",
        url="https://www.backstage.com/casting/",
        category="performing_arts",
        priority=1,
    ),

    Source(
        name="The Stage",
        url="https://www.thestage.co.uk/jobs-auditions/jobs-auditions",
        category="performing_arts",
        priority=1,
    ),

    Source(
        name="Mandy",
        url="https://www.mandy.com/",
        category="performing_arts",
        priority=1,
    ),


    # ========================================================
    # ACTING / SCREEN / CASTING
    # ========================================================

    Source(
        name="Casting Networks",
        url="https://www.castingnetworks.com/",
        category="acting",
        priority=1,
    ),

    Source(
        name="Casting Frontier",
        url="https://castingfrontier.com/",
        category="acting",
        priority=1,
    ),

    Source(
        name="AllCasting",
        url="https://allcasting.com/",
        category="acting",
        priority=2,
        automation_ready=True,
    ),

    Source(
        name="e-TALENTA",
        url="https://www.etalenta.eu/",
        category="acting",
        priority=2,
    ),

    Source(
        name="Spotlight",
        url="https://www.spotlight.com/",
        category="acting",
        priority=1,
    ),


    # ========================================================
    # CRUISE / INTERNATIONAL ENTERTAINMENT
    # ========================================================

    # Entertainers Worldwide and Backstage already cover
    # many cruise opportunities.
    Source(
        name="Celebrity Cruises Entertainment",
        url="https://www.celebritycruisesentertainment.com/online-submissions",
        category="cruise_entertainment",
        priority=1,
        automation_ready=True,
        refresh_existing=True,
    ),
    Source(
        name="AIDA Casting",
        url="https://aida.de/careers/de/casting",
        category="cruise_entertainment",
        priority=1,
        # The official site currently denies automated requests, so keep this
        # visible in the source registry without making every daily run fail.
        automation_ready=False,
    ),

    # Taylor Made is an agency/representation route. It belongs in the Agency
    # filter rather than being presented as a particular paid contract.
    Source(
        name="Taylor Made Global",
        url="https://www.taylormadeents.com/management",
        category="agency",
        priority=1,
        automation_ready=True,
    ),

    # ========================================================
    # INSTAGRAM DISCOVERY
    # ========================================================
    # These accounts are deliberately read through the official Meta API, not
    # by scraping Instagram pages.  They become automation-ready only when
    # the StageViva Meta access-token configuration is present.
    *[
        Source(
            name=f"Instagram @{handle}",
            url=f"https://www.instagram.com/{handle}/",
            category=category,
            priority=1,
            automation_ready=True,
            source_type="instagram",
            instagram_handle=handle,
            per_run_limit=1,
        )
        for handle, category in (
            ("audition360", "performing_arts"),
            ("audition_company_2026", "ballet_dance"),
            ("auditionsballet", "ballet_dance"),
            ("risingstars_talents_job4dancer", "ballet_dance"),
            ("dancingopportunities", "ballet_dance"),
            ("balletauditions", "ballet_dance"),
            ("au_di_tionscom", "performing_arts"),
            ("audition.dance", "ballet_dance"),
            ("balletplaces", "ballet_dance"),
            ("industryauditions", "performing_arts"),
            ("castingcallsuk_", "acting"),
            ("aidacasting", "cruise_entertainment"),
            ("taylormadeglobal", "agency"),
            ("bubblesanimationentertainment", "performing_arts"),
            ("starlightpsagency", "agency"),
            ("celebritycruisesentertainment", "cruise_entertainment"),
        )
    ],


    # ========================================================
    # THEATRE / MUSICAL THEATRE
    # ========================================================

    # The Stage + Backstage + Mandy currently provide
    # strong coverage.
    #
    # More specialist musical-theatre sources can be added
    # after coverage testing.


    # ========================================================
    # RESORTS / HOTELS / THEME PARKS
    # ========================================================

    # These will initially be discovered through the
    # general performing-arts sources.
    #
    # Specialist resort/theme-park sources will be added
    # as separate adapters.


    # ========================================================
    # COMMERCIAL / OTHER
    # ========================================================

    # General casting platforms above already provide
    # commercial, music video, events and live-performance
    # opportunities.
]


# ============================================================
# HELPERS
# ============================================================

def get_active_sources() -> List[Source]:
    """
    Return all currently active discovery sources.
    """

    return [
        source
        for source in SOURCES
        if source.active
    ]


def get_sources_by_category(
    category: str,
) -> List[Source]:

    return [
        source
        for source in get_active_sources()
        if source.category.lower() == category.lower()
    ]


def get_sources_by_priority(
    priority: int,
) -> List[Source]:

    return [
        source
        for source in get_active_sources()
        if source.priority == priority
    ]


def get_automated_sources() -> List[Source]:
    """Return sources with a tested adapter that are safe for daily runs."""
    # Do not make the daily source scan fail while Meta is being configured.
    from instagram_discovery import instagram_is_configured

    return [
        source
        for source in get_active_sources()
        if source.automation_ready
        and (source.source_type != "instagram" or instagram_is_configured())
    ]
