import json

from match_engine import match_artist_to_opportunity


def main():
    print("Starting match test...")

    with open("artist_dna.json", "r", encoding="utf-8") as f:
        artist = json.load(f)

    with open("opportunity.json", "r", encoding="utf-8") as f:
        opportunity = json.load(f)

    print("Artist DNA loaded.")
    print("Opportunity loaded.")
    print("Running match engine...")

    result = match_artist_to_opportunity(
        artist,
        opportunity
    )

    print("\n===== MATCH RESULT =====\n")

    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()