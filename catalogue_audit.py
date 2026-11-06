"""Read-only catalogue inventory and reviewed repair proposal. Emits JSON."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit


SARAJEVO_LISTING = "https://balletplaces.com/auditions/the-sarajevo-national-theatre-ballet-audition-november-december-2026/"
SARAJEVO_SOURCE = "https://nps.ba/novost/potreban-muski-baletski-igrac-za-angazman-u-baletu-nps/1034"
SARAJEVO_POSTER = "https://nps.ba/assets/storage/photos/356a192b7913b04c54574d18c28d46e6395428ab/viber_image_2026-10-09_10-34-23-370.jpg"
RMB_SOURCE = "https://russianmastersballet.com/courses/intensive-auditions"
RMB_SLUGS = {
    "russian-masters-ballet-online-individual-auditions-2026-2",
    "auditions-by-video-for-russian-masters-ballet-intensives-2026",
    "russian-masters-ballet-online-group-auditions-2026",
    "russian-masters-ballet-online-group-auditions-2026-1",
    "russian-masters-ballet-auditions-by-video-2026-1",
}


def _value(data, *path):
    for name in path:
        data = data.get(name) if isinstance(data, dict) else None
    return data.get("value") if isinstance(data, dict) else data


def _fields(data, prefix=""):
    for key, value in data.items():
        if key == "stageviva_review":
            continue
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and "value" in value:
            yield path, value["value"]
        elif isinstance(value, dict):
            yield from _fields(value, path)


def _missing(value):
    return value is None or value == [] or (isinstance(value, str) and value.strip().casefold() in {"", "unknown", "not specified", "n/a"})


def _repair(row):
    listing = row["listing_url"]
    host = urlsplit(listing).hostname
    slug = urlsplit(listing).path.rstrip("/").rsplit("/", 1)[-1]
    if listing.rstrip("/") == SARAJEVO_LISTING.rstrip("/"):
        return {
            "kind": "repair_application_and_extraction",
            "source": SARAJEVO_SOURCE,
            "poster": SARAJEVO_POSTER,
            "visible": bool(row["visible"]),
            "fields": {
                "identity.title": "Male Corps de Ballet Dancer",
                "identity.organisation": "Sarajevo National Theatre Ballet",
                "identity.opportunity_type": "Professional ballet company position",
                "location.city": "Sarajevo", "location.country": "Bosnia and Herzegovina",
                "dates.published": "2026-10-09", "dates.application_deadline": "unknown", "dates.audition_dates": [],
                "requirements.experience.required_level": "At least two years of work experience and a ballet school diploma",
                "requirements.physical.gender_requirement": "Male dancer",
                "requirements.physical.height_requirement": "Minimum height: 180 cm",
                "requirements.styles": ["Classical ballet", "Neoclassical", "Contemporary"],
                "application.method": "Email the ballet company with your materials.",
                "application.materials": ["CV", "Portrait photo", "Full-body photo", "Short video"],
                "application.email": "balet@nps.ba", "application.application_url": "unknown",
                "source.official_url": SARAJEVO_SOURCE,
            },
            "review": {"decision": "VALID_DANCER_OPPORTUNITY", "selection_outcome": "Corps de ballet position",
                       "pathway": {"kind": "email", "target": "balet@nps.ba"},
                       "evidence": ["Official theatre announcement and recruitment poster, 9 October 2026."],
                       "availability": "Announcement accessible. No exact closing date published."},
        }
    if host == "ballee.co" and slug in RMB_SLUGS:
        return {"kind": "exclude_training_admission", "source": RMB_SOURCE, "visible": False,
                "hidden_reason": "school_or_training",
                "review": {"decision": "INVALID_NON_DANCER_OPPORTUNITY", "selection_outcome": "Admission to a ballet intensive",
                           "evidence": ["The organiser describes auditions as selection for training groups and course scholarships."]}}
    return None


def audit(path: Path, *, offset: int = 0, limit: int | None = None) -> dict:
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as database:
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA query_only=ON")
        database.execute("BEGIN")
        rows = database.execute("""
            SELECT o.*, (SELECT count(*) FROM match_results m WHERE m.opportunity_id=o.id) AS match_count
            FROM opportunities o ORDER BY o.id
        """).fetchall()
        rejections = [dict(row) for row in database.execute("SELECT * FROM rejected_listings ORDER BY listing_url")]
    records = []
    for row in rows:
        opportunity = json.loads(row["opportunity_json"])
        review = opportunity.get("stageviva_review", {})
        fields = dict(_fields(opportunity))
        findings = []
        destination = _value(opportunity, "application", "application_url")
        if isinstance(destination, str) and any(part in destination.casefold() for part in ("?aff=", "&aff=")):
            findings.append("Affiliate parameter in stored application link")
        if _missing(_value(opportunity, "dates", "application_deadline")):
            findings.append("No usable stored deadline")
        proposal = _repair(row)
        records.append({
            "id": row["id"], "title": row["title"], "listing_url": row["listing_url"],
            "source_name": row["source_name"], "source_url": row["source_url"],
            "source_snapshot_available": bool(review.get("sources")),
            "source_availability": "reviewed_evidence" if proposal else "unverified",
            "visible": bool(row["visible"]), "hidden_reason": row["hidden_reason"],
            "publication_status": _value(opportunity, "status") or "unknown",
            "classification": review.get("decision", "legacy_unreviewed"),
            "extracted_fields": {key: value for key, value in fields.items() if not _missing(value)},
            "missing_fields": [key for key, value in fields.items() if _missing(value)],
            "application_url": destination, "matching_evaluations": row["match_count"],
            "findings": findings, "proposal": proposal,
            "original_sha256": hashlib.sha256(row["opportunity_json"].encode()).hexdigest(),
        })
    reviewed = [record for record in records if record["proposal"]]
    stats = {
        "total": len(records), "visible": sum(record["visible"] for record in records),
        "projected_visible": sum(record["proposal"]["visible"] if record["proposal"] else record["visible"] for record in records),
        "hidden_reasons": Counter(record["hidden_reason"] or "unspecified" for record in records if not record["visible"]),
        "rejections": Counter(record["reason"] for record in rejections),
        "match_evaluations": sum(record["matching_evaluations"] for record in records),
        "reviewed_proposals": Counter(record["proposal"]["kind"] for record in reviewed),
        "awaiting_source_review": len(records) - len(reviewed),
    }
    return {"stats": stats, "records": records[offset:None if limit is None else offset + limit], "rejections": rejections}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--summary", action="store_true")
    arguments = parser.parse_args()
    result = audit(arguments.database)
    print(json.dumps(result["stats"] if arguments.summary else result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
