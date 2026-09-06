"""Email delivery adapter for StageViva's notification outbox."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from dotenv import load_dotenv

from storage import StageVivaStorage


class EmailDeliveryError(RuntimeError):
    pass


def _email_content(notification: dict[str, Any]) -> tuple[str, str]:
    overall = notification["match"].get("overall", {})
    score = overall.get("match_score", "New")
    subject = f"StageViva: {score}% match — {notification['title']}"
    body = (
        f"Hi {notification['display_name']},\n\n"
        f"StageViva found a new opportunity: {notification['title']}.\n"
        f"Match score: {score}% ({overall.get('recommendation', 'new opportunity')}).\n\n"
        f"{overall.get('summary', '')}\n\n"
        f"View the original listing: {notification['listing_url']}\n"
    )
    return subject, body


def _send_with_resend(notification: dict[str, Any], api_key: str, from_email: str) -> None:
    subject, body = _email_content(notification)
    request = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps({"from": from_email, "to": [notification["email"]], "subject": subject, "text": body}).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "StageViva/0.1",
            "Idempotency-Key": notification["id"],
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status not in {200, 201}:
                raise EmailDeliveryError(f"Email provider returned HTTP {response.status}.")
    except urllib.error.URLError as error:
        raise EmailDeliveryError(f"Could not deliver notification email: {error}") from error


def deliver_pending_emails(storage: StageVivaStorage, *, limit: int = 50, dry_run: bool = False) -> dict[str, Any]:
    """Send pending email notifications, or preview them in dry-run mode."""
    load_dotenv()
    notifications = storage.pending_email_notifications(limit)
    if dry_run:
        return {"pending": len(notifications), "sent": 0, "preview": [
            {"id": item["id"], "to": item["email"], "subject": _email_content(item)[0]}
            for item in notifications
        ]}
    api_key = os.getenv("RESEND_API_KEY")
    from_email = os.getenv("RESEND_FROM_EMAIL")
    if not api_key or not from_email:
        raise EmailDeliveryError("Set RESEND_API_KEY and RESEND_FROM_EMAIL before sending email.")
    sent = 0
    failures: list[str] = []
    for notification in notifications:
        try:
            _send_with_resend(notification, api_key, from_email)
            storage.mark_notification_email_sent(notification["id"])
            sent += 1
        except EmailDeliveryError as error:
            failures.append(f"{notification['id']}: {error}")
    return {"pending": len(notifications), "sent": sent, "failures": failures}
