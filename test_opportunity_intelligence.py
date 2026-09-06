"""A runnable smoke test using the Leipzig Ballet listing."""

import json

from opportunity_intelligence import DiscoveryContext, analyse_opportunity

LEIPZIG_URL = "https://balletplaces.com/auditions/leipzig-ballet-audition-13-september-2026/"


def main() -> None:
    result = analyse_opportunity(
        LEIPZIG_URL,
        DiscoveryContext(
            source_name="BalletPlaces",
            source_url="https://balletplaces.com/ballet-auditions/accepting-applications/",
            category="ballet_dance",
            title="Leipzig Ballet Audition",
        ),
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
