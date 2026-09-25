"""Direct Apple Push Notification service delivery for the native StageViva app."""

from __future__ import annotations

import base64
import json
import os
import time
from typing import Any

import httpx
import jwt

from storage import StageVivaStorage


class NativePushConfigurationError(RuntimeError):
    """The service has not yet been given its Apple APNs credential."""


class NativePushDeliveryError(RuntimeError):
    """APNs refused a test notification after the device had registered."""


def _private_key() -> str:
    encoded = os.getenv("STAGEVIVA_APNS_PRIVATE_KEY_BASE64", "").strip()
    if encoded:
        try:
            return base64.b64decode(encoded).decode("utf-8")
        except Exception as error:
            raise NativePushConfigurationError("The APNs private key is not valid base64.") from error
    return os.getenv("STAGEVIVA_APNS_PRIVATE_KEY", "").replace("\\n", "\n").strip()


def _credentials() -> tuple[str, str, str, str]:
    team_id = os.getenv("STAGEVIVA_APPLE_TEAM_ID", "").strip()
    key_id = os.getenv("STAGEVIVA_APNS_KEY_ID", "").strip()
    private_key = _private_key()
    topic = os.getenv("STAGEVIVA_IOS_BUNDLE_ID", "com.stageviva.app").strip()
    if not all((team_id, key_id, private_key, topic)):
        raise NativePushConfigurationError("Apple Push credentials have not been configured on the server.")
    return team_id, key_id, private_key, topic


def _send_apns(token: str, payload: dict[str, Any]) -> tuple[bool, str | None]:
    team_id, key_id, private_key, topic = _credentials()
    provider_token = jwt.encode(
        {"iss": team_id, "iat": int(time.time())},
        private_key,
        algorithm="ES256",
        headers={"kid": key_id},
    )
    host = os.getenv("STAGEVIVA_APNS_HOST", "https://api.push.apple.com").rstrip("/")
    headers = {
        "authorization": f"bearer {provider_token}",
        "apns-topic": topic,
        "apns-push-type": "alert",
        "apns-priority": "10",
    }
    with httpx.Client(http2=True, timeout=15.0) as client:
        response = client.post(f"{host}/3/device/{token}", headers=headers, content=json.dumps(payload))
    if response.status_code == 200:
        return True, None
    reason = "unknown"
    try:
        reason = str(response.json().get("reason") or reason)
    except ValueError:
        pass
    return False, f"{response.status_code}:{reason}"


def _match_payload(title: str, match: dict[str, Any], *, is_basic: bool = False) -> dict[str, Any]:
    score = match.get("overall", {}).get("match_score", "New")
    return {
        "aps": {
            "alert": {
                "title": "New StageViva match",
                "body": f"An opportunity matches you at {score}%" if is_basic else f"{title} — {score}% match",
            },
            "sound": "default",
        },
        "stageviva_path": "/matches",
    }


def deliver_pending_native_push_notifications(storage: StageVivaStorage, *, limit: int = 100) -> dict[str, Any]:
    """Send every queued match once to each native iOS device that opted in."""
    pending = storage.pending_native_push_notifications(limit)
    if not pending:
        return {"configured": True, "pending": 0, "sent": 0, "removed": 0, "failures": []}
    try:
        _credentials()
    except NativePushConfigurationError as error:
        return {"configured": False, "pending": len(pending), "sent": 0, "removed": 0, "failures": [str(error)]}

    sent = removed = 0
    failures: list[str] = []
    invalid_reasons = {"BadDeviceToken", "DeviceTokenNotForTopic", "Unregistered"}
    for item in pending:
        if item["platform"] != "ios":
            continue
        try:
            delivered, reason = _send_apns(
                item["token"], _match_payload(
                    item["title"], item["match"],
                    is_basic=str(item.get("membership_tier") or "").lower() == "free",
                ),
            )
        except Exception as error:
            failures.append(f"{item['token_id']}: {error}")
            continue
        if delivered:
            storage.mark_push_delivered(item["notification_id"], item["token_id"])
            sent += 1
        elif reason and any(reason.endswith(f":{value}") for value in invalid_reasons):
            storage.remove_native_push_token_by_id(item["token_id"])
            removed += 1
        else:
            failures.append(f"{item['token_id']}: {reason or 'delivery failed'}")
    return {"configured": True, "pending": len(pending), "sent": sent, "removed": removed, "failures": failures}


def send_test_native_push_notification(storage: StageVivaStorage, user_id: str) -> dict[str, int]:
    """Send a user-requested test alert to the caller's registered iPhone(s)."""
    _credentials()
    sent = 0
    failures: list[str] = []
    for device in storage.list_native_push_tokens_for_user(user_id):
        if device["platform"] != "ios":
            continue
        delivered, reason = _send_apns(device["token"], {
            "aps": {
                "alert": {
                    "title": "StageViva notifications are on",
                    "body": "You will hear about new matches here.",
                },
                "sound": "default",
            },
            "stageviva_path": "/matches",
        })
        if delivered:
            sent += 1
        elif reason and any(reason.endswith(f":{value}") for value in {"BadDeviceToken", "DeviceTokenNotForTopic", "Unregistered"}):
            storage.remove_native_push_token_by_id(device["id"])
            failures.append(reason)
        elif reason:
            failures.append(reason)
    if not sent:
        detail = ", ".join(failures) if failures else "No iPhone notification token is registered yet."
        raise NativePushDeliveryError(detail)
    return {"sent": sent}
