"""One publication policy for fresh extractions and previously held imports."""

import copy
import re
from urllib.parse import urlsplit

from opportunity_quality import VALID, REVIEW, application_pathway, review_reason, safe_official_website, supported_excerpt, unwrap_source_urls, valid_application_email, value_at
from opportunity_source_facts import complete_source_facts
from opportunity_lifecycle import is_current_opportunity
from opportunity_policy import is_non_performer_opportunity, is_school_or_training_opportunity

PUBLICATION_POLICY = "source-ready-v5"


def _recover_role_evidence(result: dict) -> None:
    """Reuse verified recruitment facts when the model omitted its role quote."""
    review = result.get("stageviva_review", {})
    if (review.get("decision") != REVIEW or review.get("extraction_decision") != VALID
            or review.get("evidence") or review.get("rejected_decision_evidence")
            or review.get("conflicts") or review.get("blocking_reasons")):
        return
    sources = review.get("sources") or []
    text = "\n".join(str(source.get("text", "")) for source in sources if isinstance(source, dict))
    identity = result.get("identity", {})
    for key in ("title", "organisation"):
        field = identity.get(key, {})
        if (not isinstance(field, dict) or str(field.get("value", "")).strip().casefold() in {"", "unknown", "not specified", "n/a"}
                or not any(supported_excerpt(str(quote), text) for quote in field.get("evidence", []))):
            return
    for key in ("title", "description", "opportunity_type"):
        field = identity.get(key, {})
        for quote in field.get("evidence", []) if isinstance(field, dict) else []:
            if not isinstance(quote, str) or not supported_excerpt(quote, text):
                continue
            if re.search(r"\b(?:seeking|looking\s+for|recruiting|hiring)\b.{0,100}\b(?:dancers?|dance\s+artists?)\b", " ".join(quote.split()), re.I):
                review.update(decision=VALID, evidence=[quote], role_evidence_recovered_from_fields=True)
                return


def _resolved_application(result: dict, *, recovered: bool = False) -> dict:
    review = result.get("stageviva_review", {})
    blockers = review.get("blocking_reasons") or []
    if (review.get("decision") == VALID and application_pathway(result)
            and ("application_missing" in blockers or recovered)):
        review["blocking_reasons"] = [reason for reason in blockers if reason != "application_missing"]
        if "application_missing" in blockers:
            review["resolved_blockers"] = list(dict.fromkeys([*(review.get("resolved_blockers") or []), "application_missing"]))
        if recovered:
            review["application_recovered_from_source"] = True
        if (not review["blocking_reasons"] and not review.get("conflicts")
                and all(str(value_at(result, "identity", key) or "").strip().casefold() not in {"", "unknown", "not specified", "n/a"} for key in ("title", "organisation"))):
            review["ready_for_publication"] = True
    return result


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
    _recover_role_evidence(result)
    review = result.get("stageviva_review", {})
    if review.get("decision") != VALID or application_pathway(result):
        return _resolved_application(result)
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
            return _resolved_application(result, recovered=True)
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
    return _resolved_application(result, recovered=bool(application_pathway(result)))


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
