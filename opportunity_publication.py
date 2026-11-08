"""One publication policy for fresh extractions and previously held imports."""

import copy
import re
from urllib.parse import urlsplit

from opportunity_quality import VALID, application_pathway, review_reason, safe_official_website, unwrap_source_urls, valid_application_email
from opportunity_source_facts import complete_source_facts
from opportunity_lifecycle import is_current_opportunity
from opportunity_policy import is_non_performer_opportunity, is_school_or_training_opportunity

PUBLICATION_POLICY = "source-ready-v3"


def _application_emails(text):
    matches = []
    for match in re.finditer(r"[A-Za-z0-9._+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}", text):
        prefix = text[max(0, match.start() - 160):match.start()]
        if re.search(r"\b(?:questions|enquiries|inquiries|tickets|press|contact)\b", prefix[-80:], re.I):
            continue
        if re.search(r"\b(?:send|submit)\s+(?:your\s+)?applications?\b[^.!?]{0,100}$|\bapply\b[^.!?]{0,100}$|\bapplications?\s*(?:(?:by|via)\s+e-?mail\s*)?(?:to|:)[^\w@]{0,15}$", prefix, re.I):
            matches.append((match, prefix))
    return matches


def prepare_publication(opportunity: dict, reference_url: str = "") -> dict:
    result = copy.deepcopy(complete_source_facts(opportunity))
    review = result.get("stageviva_review", {})
    if review.get("decision") != VALID or application_pathway(result):
        return result
    review = result["stageviva_review"]
    sources = review.get("sources") or []
    text = "\n".join(str(source.get("text", "")) for source in sources if isinstance(source, dict))
    # A missed literal email can be recovered from application instructions,
    # never from an arbitrary contact/footer address.
    candidates = _application_emails(text)
    for match, prefix in candidates:
        if valid_application_email(match.group()):
            if len({candidate.group().casefold() for candidate, _ in candidates}) != 1:
                break
            quote = prefix.strip() + " " + match.group()
            review["pathway"] = {"kind": "email", "target": match.group(), "evidence": [quote]}
            result.setdefault("application", {})["email"] = {"value": match.group(), "evidence": [quote], "confidence": "high"}
            return result
    # Employer-website fallback must be explicitly supplied and actually
    # retrieved. A directory or arbitrary first link is not promoted.
    normalized = unwrap_source_urls(text)
    normalized = re.sub(r"(\b(?:website|official\s+site)\s*:?\s*)(?!https?://|www\.)([A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,63}(?:/[^\s<>\"']*)?)", r"\1https://\2", normalized, flags=re.I)
    normalized = unwrap_source_urls(normalized)
    labelled = {url.rstrip(".,;:!?)") for url in re.findall(r"\b(?:official\s+(?:website|site)|website)\s*:?\s*(https?://[^\s<>\"']+)", normalized, re.I)}
    if reference_url:
        labelled.add(reference_url)
    for source in sources:
        if not isinstance(source, dict):
            continue
        url = source.get("url", "")
        if source.get("type") == "caption" or not safe_official_website(url):
            continue
        def same_site_path(left, right):
            first, second = urlsplit(left), urlsplit(right)
            return ((first.hostname or "").lower().removeprefix("www."), first.path.rstrip("/"), first.query) == ((second.hostname or "").lower().removeprefix("www."), second.path.rstrip("/"), second.query)
        if source.get("requested_url") not in labelled and not any(same_site_path(url, labelled_url) for labelled_url in labelled):
            continue
        quote = str(source.get("text", ""))[:250].strip()
        if not quote:
            continue
        review["pathway"] = {"kind": "official_website", "target": url, "evidence": [quote]}
        result.setdefault("source", {})["official_url"] = {"value": url, "evidence": [quote], "confidence": "high"}
        break
    return result


def publication_reason(opportunity: dict) -> str | None:
    review = opportunity.get("stageviva_review", {})
    reason = review_reason(opportunity)
    if review.get("decision") != VALID:
        return reason or "needs_verification"
    if review.get("conflicts"):
        return "source_conflict"
    if review.get("ready_for_publication") is False or review.get("blocking_reasons"):
        return "needs_information"
    if reason:
        return reason
    if is_non_performer_opportunity(opportunity):
        return "non_dancer_opportunity"
    if not is_current_opportunity(opportunity):
        return "expired"
    if is_school_or_training_opportunity(opportunity):
        return "school"
    # Older AI results have no readiness field. Keep low-confidence core
    # facts held, but do not confuse optional missing fields with readiness.
    if "ready_for_publication" not in review:
        core = opportunity.get("identity", {})
        if opportunity.get("overall_confidence") not in {"high", "very_high"} and not all(
            isinstance(core.get(key), dict) and core[key].get("confidence") in {"high", "very_high"} and core[key].get("evidence")
            for key in ("title", "organisation")
        ):
            return "needs_information"
    return None
