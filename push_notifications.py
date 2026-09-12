"""Web Push delivery for StageViva's installed mobile app."""

from __future__ import annotations

import base64
import json
from typing import Any

from pywebpush import WebPushException, webpush

from storage import StageVivaStorage


def deliver_pending_push_notifications(storage: StageVivaStorage, *, limit: int = 100) -> dict[str, Any]:
    """Deliver each new match once to every currently subscribed device."""
    private_key = storage.get_or_create_setting("web_push_vapid_private_key", _new_vapid_private_key)
    pending = storage.pending_push_notifications(limit)
    sent = 0
    removed = 0
    failures: list[str] = []
    for item in pending:
        score = item["match"].get("overall", {}).get("match_score", "New")
        payload = {
            "title": "New StageViva match",
            "body": f"{item['title']} — {score}% match",
            "url": "/matches",
            "tag": item["notification_id"],
        }
        try:
            webpush(
                subscription_info=item["subscription"],
                data=json.dumps(payload),
                vapid_private_key=private_key,
                vapid_claims={"sub": "mailto:hello@stageviva.com"},
            )
            storage.mark_push_delivered(item["notification_id"], item["subscription_id"])
            sent += 1
        except WebPushException as error:
            # Browsers return 404/410 for an expired device subscription. It is
            # safe to remove it, and the app will register a replacement next open.
            status_code = getattr(getattr(error, "response", None), "status_code", None)
            if status_code in {404, 410}:
                storage.remove_push_subscription_by_id(item["subscription_id"])
                removed += 1
            else:
                failures.append(f"{item['notification_id']}: {error}")
    return {"pending": len(pending), "sent": sent, "removed": removed, "failures": failures}


def _new_vapid_private_key() -> str:
    """Generate an RFC-compatible URL-safe VAPID private key once per service."""
    from cryptography.hazmat.primitives.asymmetric import ec

    private_key = ec.generate_private_key(ec.SECP256R1())
    raw = private_key.private_numbers().private_value.to_bytes(32, "big")
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def public_vapid_key(storage: StageVivaStorage) -> str:
    """Return the public key the browser needs when it creates a subscription."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    encoded_private = storage.get_or_create_setting("web_push_vapid_private_key", _new_vapid_private_key)
    raw = base64.urlsafe_b64decode(encoded_private + "=" * (-len(encoded_private) % 4))
    private_number = int.from_bytes(raw, "big")
    key = ec.derive_private_key(private_number, ec.SECP256R1())
    public_raw = key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(public_raw).decode().rstrip("=")
