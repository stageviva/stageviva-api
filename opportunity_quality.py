"""Evidence and publication rules shared by extraction, review and presentation."""

import re
import unicodedata
from urllib.parse import parse_qsl, urlsplit

from public_source import public_url


VALID = "VALID_DANCER_OPPORTUNITY"
INVALID = "INVALID_NON_DANCER_OPPORTUNITY"
REVIEW = "NEEDS_VERIFICATION"
PATHWAY_LABELS = {
    "direct_form": "Apply Now",
    "official_announcement": "View Official Audition",
    "email": "Apply by Email",
    "relevant_listing": "View Application Instructions",
    "official_website": "Visit official website",
}


def value_at(document: dict, *keys: str):
    for key in keys:
        if not isinstance(document, dict):
            return None
        document = document.get(key)
    return document.get("value") if isinstance(document, dict) else document


def safe_application_url(url: str) -> bool:
    if not public_url(url):
        return False
    target = urlsplit(url)
    path = target.path.strip("/").casefold()
    if path in {"", "login", "signin", "sign-in", "register", "contact", "shop"}:
        return False
    if any(key.casefold() in {"aff", "affiliate", "aff_id"} for key, _ in parse_qsl(target.query)):
        return False
    return True


def valid_application_email(address: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9._+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}", address))


def unwrap_source_urls(text: str) -> str:
    """Join a URL wrapped at a delimiter, preserving a genuine path hyphen."""
    return re.sub(r"((?:https?://|www\.)[^\s<>\"']*[-/?=&])\r?\n[ \t]*([A-Za-z0-9][A-Za-z0-9._~%/?=&+#-]*)[ \t]*(?=\r?$|\n)", r"\1\2", text, flags=re.M)


def safe_official_website(url: str) -> bool:
    if not public_url(url):
        return False
    target = urlsplit(url)
    host = (target.hostname or "").lower().removeprefix("www.")
    if any(key.casefold() in {"aff", "affiliate", "aff_id"} for key, _ in parse_qsl(target.query)):
        return False
    if target.path.strip("/").casefold() in {"privacy", "privacy-policy", "terms", "terms-and-conditions", "cookies"}:
        return False
    return (host not in {"instagram.com", "facebook.com", "balletplaces.com", "danceeurope.net", "ballee.co", "allcasting.com", "entertainersworldwidejobs.com"}
            and (not target.path.strip("/") or safe_application_url(url)))


def supported_excerpt(excerpt: str, source_text: str) -> bool:
    normalize = lambda value: " ".join(unwrap_source_urls(unicodedata.normalize("NFKC", value).replace("\u00ad", "")).casefold().split())
    quote = normalize(excerpt)
    return len(quote) >= 4 and quote in normalize(source_text)


def validate_shape(value, schema: dict) -> bool:
    """Validate the subset used by the strict extraction schema locally."""
    kind = schema["type"]
    if kind == "object":
        properties = schema["properties"]
        return (isinstance(value, dict) and value.keys() == properties.keys()
                and all(validate_shape(value[key], rule) for key, rule in properties.items()))
    if kind == "array":
        return isinstance(value, list) and all(validate_shape(item, schema["items"]) for item in value)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "integer":
        return type(value) is int and schema.get("minimum", value) <= value <= schema.get("maximum", value)
    return isinstance(value, str) and ("enum" not in schema or value in schema["enum"])


def apply_source_evidence(opportunity: dict, text: str) -> None:
    """Clear extracted facts that have no source excerpt. Preserve their field shape."""
    for field in opportunity.values():
        if isinstance(field, list):
            for item in field:
                if isinstance(item, dict) and "value" in item and "evidence" in item:
                    apply_source_evidence({"item": item}, text)
            continue
        if not isinstance(field, dict):
            continue
        if "evidence" not in field:
            apply_source_evidence(field, text)
            continue
        field["evidence"] = [quote for quote in field["evidence"] if supported_excerpt(quote, text)]
        if field["evidence"]:
            continue
        field["confidence"] = "unknown"
        if "required_level" in field:
            field.update(required_level="unknown", assessment_basis="not_specified")
        elif "value" in field:
            original = field["value"]
            field["value"] = [] if isinstance(original, list) else None if isinstance(original, bool) else "unknown"


def application_pathway(opportunity: dict) -> dict:
    review = opportunity.get("stageviva_review", {})
    pathway = review.get("pathway", {})
    kind = pathway.get("kind")
    target = pathway.get("target", "")
    if kind == "email" and isinstance(target, str) and valid_application_email(target):
        return pathway
    if kind == "official_website" and isinstance(target, str) and safe_official_website(target):
        return pathway
    if kind in PATHWAY_LABELS and kind not in {"email", "official_website"} and isinstance(target, str) and safe_application_url(target):
        return pathway
    return {}


def review_reason(opportunity: dict) -> str | None:
    """A legacy record has no structured decision and keeps its legacy policy."""
    review = opportunity.get("stageviva_review")
    if not isinstance(review, dict):
        return None
    decision = review.get("decision")
    if decision == INVALID:
        return "non_dancer_opportunity"
    if decision != VALID:
        return "needs_verification"
    if not application_pathway(opportunity):
        return "unverified_application"
    if any(str(value_at(opportunity, "identity", key) or "").strip().casefold() in {"", "unknown", "not specified", "n/a"}
           for key in ("title", "organisation")):
        return "needs_verification"
    return None


def confirm_editorial_review(opportunity: dict) -> None:
    """Record the administrator's explicit confirmation of role and application."""
    email = str(value_at(opportunity, "application", "email") or "").strip()
    url = str(value_at(opportunity, "application", "application_url") or "").strip()
    pathway = {}
    if valid_application_email(email):
        pathway = {"kind": "email", "target": email}
    elif safe_application_url(url):
        pathway = {"kind": "relevant_listing", "target": url}
    review = opportunity.setdefault("stageviva_review", {})
    review.setdefault("original_decision", review.get("decision"))
    review.setdefault("original_pathway", review.get("pathway"))
    review.update(decision=VALID, pathway=pathway, editor_confirmed=True)
