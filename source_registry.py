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
    #
    # We can add more cruise-specific discovery sources
    # once their public listing structure has been tested.


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
    return [
        source
        for source in get_active_sources()
        if source.automation_ready
    ]
