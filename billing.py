"""Small, explicit Stripe boundary for StageViva web subscriptions.

The module intentionally uses Stripe's HTTPS API directly.  Keeping the
boundary this small makes it easy to mock every outbound request in tests and
ensures that no test can contact Stripe unless the dedicated network switch is
enabled.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


STRIPE_API_BASE = "https://api.stripe.com/v1"
SUPPORTED_PLANS = frozenset({"premium_monthly", "audition_season"})


class BillingConfigurationError(RuntimeError):
    """Raised when billing is not deliberately and safely configured."""


class BillingProviderError(RuntimeError):
    """Raised when Stripe rejects or cannot complete a request."""


class BillingSignatureError(ValueError):
    """Raised when a webhook did not originate from Stripe."""


def _truthy(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def stripe_mode() -> str:
    mode = os.getenv("STAGEVIVA_STRIPE_MODE", "test").strip().lower()
    return mode if mode in {"test", "live"} else "test"


def stripe_requests_enabled() -> bool:
    return _truthy("STAGEVIVA_STRIPE_REQUESTS_ENABLED")


def plan_prices() -> dict[str, str]:
    return {
        "premium_monthly": os.getenv("STRIPE_PREMIUM_MONTHLY_PRICE_ID", "").strip(),
        "audition_season": os.getenv("STRIPE_AUDITION_SEASON_PRICE_ID", "").strip(),
    }


def plan_for_price(price_id: str) -> str | None:
    for plan, configured_price in plan_prices().items():
        if configured_price and hmac.compare_digest(configured_price, str(price_id or "")):
            return plan
    return None


def _secret_key() -> str:
    key = os.getenv("STRIPE_SECRET_KEY", "").strip()
    if not key:
        raise BillingConfigurationError("Stripe has not been configured yet.")
    expected_prefix = "sk_live_" if stripe_mode() == "live" else "sk_test_"
    if not key.startswith(expected_prefix):
        raise BillingConfigurationError(
            f"The Stripe key does not match StageViva's {stripe_mode()} billing mode."
        )
    return key


def _public_app_url() -> str:
    value = os.getenv("STAGEVIVA_PUBLIC_APP_URL", "https://stageviva.com").strip().rstrip("/")
    if not value.startswith("https://"):
        raise BillingConfigurationError("StageViva's public billing URL must use HTTPS.")
    return value


def _stripe_api_request(
    path: str, fields: dict[str, Any], *, method: str = "POST",
) -> dict[str, Any]:
    """Make one guarded Stripe request; tests replace this function entirely."""
    if not stripe_requests_enabled():
        raise BillingConfigurationError("Stripe requests are paused while StageViva is configuring billing.")
    key = _secret_key()
    encoded_fields = {key: value for key, value in fields.items() if value is not None}
    request = Request(
        f"{STRIPE_API_BASE}/{path.lstrip('/')}",
        data=urlencode(encoded_fields).encode("utf-8") if encoded_fields or method == "POST" else None,
        method=method,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Idempotency-Key": str(uuid.uuid4()),
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        try:
            detail = json.loads(error.read().decode("utf-8")).get("error", {}).get("message")
        except (json.JSONDecodeError, UnicodeDecodeError):
            detail = None
        raise BillingProviderError(str(detail or "Stripe rejected the request.")) from error
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise BillingProviderError("Stripe could not be reached. Please try again.") from error
    if not isinstance(result, dict):
        raise BillingProviderError("Stripe returned an invalid response.")
    return result


def create_checkout_session(
    *, user_id: str, email: str, plan: str, stripe_customer_id: str | None = None,
) -> dict[str, Any]:
    if plan not in SUPPORTED_PLANS:
        raise ValueError("Unknown StageViva membership plan.")
    price_id = plan_prices().get(plan, "")
    if not price_id:
        raise BillingConfigurationError("This StageViva plan has not been configured in Stripe.")
    app_url = _public_app_url()
    fields: dict[str, Any] = {
        "mode": "subscription",
        "client_reference_id": user_id,
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": 1,
        "success_url": f"{app_url}/membership?checkout=success&session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{app_url}/membership?checkout=cancelled",
        "metadata[stageviva_user_id]": user_id,
        "metadata[stageviva_plan]": plan,
        "subscription_data[metadata][stageviva_user_id]": user_id,
        "subscription_data[metadata][stageviva_plan]": plan,
        "billing_address_collection": "auto",
        "allow_promotion_codes": "false",
    }
    if stripe_customer_id:
        fields["customer"] = stripe_customer_id
    else:
        fields["customer_email"] = email
    return _stripe_api_request("checkout/sessions", fields)


def create_customer_portal_session(stripe_customer_id: str) -> dict[str, Any]:
    if not stripe_customer_id:
        raise ValueError("No Stripe customer is connected to this StageViva account.")
    return _stripe_api_request("billing_portal/sessions", {
        "customer": stripe_customer_id,
        "return_url": f"{_public_app_url()}/membership",
    })


def retrieve_subscription(subscription_id: str) -> dict[str, Any]:
    if not stripe_requests_enabled():
        raise BillingConfigurationError("Stripe requests are paused while StageViva is configuring billing.")
    key = _secret_key()
    request = Request(
        f"{STRIPE_API_BASE}/subscriptions/{subscription_id}",
        headers={"Authorization": f"Bearer {key}"},
    )
    try:
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise BillingProviderError("Stripe could not confirm the subscription.") from error
    if not isinstance(result, dict):
        raise BillingProviderError("Stripe returned an invalid subscription.")
    return result


def cancel_subscription(subscription_id: str) -> dict[str, Any]:
    return _stripe_api_request(f"subscriptions/{subscription_id}", {"cancel_at_period_end": "true"})


def cancel_subscription_immediately(subscription_id: str) -> dict[str, Any]:
    return _stripe_api_request(f"subscriptions/{subscription_id}", {}, method="DELETE")


def verify_webhook(payload: bytes, signature_header: str, *, tolerance_seconds: int = 300) -> dict[str, Any]:
    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise BillingConfigurationError("Stripe webhook verification has not been configured.")
    values: dict[str, list[str]] = {}
    for part in signature_header.split(","):
        key, separator, value = part.strip().partition("=")
        if separator:
            values.setdefault(key, []).append(value)
    try:
        timestamp = int(values["t"][0])
    except (KeyError, ValueError, IndexError) as error:
        raise BillingSignatureError("Stripe signature timestamp is missing.") from error
    if abs(int(time.time()) - timestamp) > tolerance_seconds:
        raise BillingSignatureError("Stripe webhook timestamp is outside the allowed window.")
    signed_payload = f"{timestamp}.".encode("utf-8") + payload
    expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in values.get("v1", [])):
        raise BillingSignatureError("Stripe webhook signature is invalid.")
    try:
        event = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BillingSignatureError("Stripe webhook payload is invalid.") from error
    if not isinstance(event, dict) or not isinstance(event.get("id"), str):
        raise BillingSignatureError("Stripe webhook event is invalid.")
    return event


def subscription_details(subscription: dict[str, Any]) -> dict[str, Any]:
    items = subscription.get("items", {}).get("data", [])
    item = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
    price = item.get("price") if isinstance(item.get("price"), dict) else {}
    metadata = subscription.get("metadata") if isinstance(subscription.get("metadata"), dict) else {}
    period_end = subscription.get("current_period_end", item.get("current_period_end"))
    return {
        "stripe_subscription_id": str(subscription.get("id") or ""),
        "stripe_customer_id": str(subscription.get("customer") or ""),
        "price_id": str(price.get("id") or ""),
        "status": str(subscription.get("status") or "unknown"),
        "current_period_end": int(period_end) if isinstance(period_end, (int, float)) else None,
        "cancel_at_period_end": bool(subscription.get("cancel_at_period_end", False)),
        "user_id": str(metadata.get("stageviva_user_id") or ""),
        "plan": str(metadata.get("stageviva_plan") or ""),
    }
