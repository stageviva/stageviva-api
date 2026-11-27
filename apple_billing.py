"""StoreKit boundary: verified Apple signatures plus current server status.

Client product IDs, prices and entitlement flags never grant membership.
Network requests require deliberate server configuration. No signing keys or
receipts are returned to clients or written to logs.
"""
from __future__ import annotations

import os
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from appstoreserverlibrary.api_client import AppStoreServerAPIClient
from appstoreserverlibrary.models.Environment import Environment
from appstoreserverlibrary.signed_data_verifier import SignedDataVerifier, VerificationException

BUNDLE_ID = "com.stageviva.app"
APP_ID = 6812465881
PRODUCT_ID = "stageviva.premium.monthly"
ROOT_PATH = Path(__file__).parent / "certificates" / "AppleRootCA-G3.cer"


class AppleConfigurationError(RuntimeError):
    pass


class AppleVerificationError(ValueError):
    pass


class AppleProviderError(RuntimeError):
    pass


def account_token(user_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://stageviva.com/apple-account/{user_id}"))


def enabled() -> bool:
    return os.getenv("STAGEVIVA_APPLE_PURCHASES_ENABLED", "false").lower() == "true"


def configured() -> bool:
    return enabled() and ROOT_PATH.is_file() and all(os.getenv(name, "").strip() for name in (
        "APPLE_IAP_KEY_ID", "APPLE_IAP_ISSUER_ID", "APPLE_IAP_PRIVATE_KEY_PATH",
    )) and Path(os.getenv("APPLE_IAP_PRIVATE_KEY_PATH", "")).is_file()


@lru_cache(maxsize=2)
def _verifier(environment: Environment) -> SignedDataVerifier:
    return SignedDataVerifier([ROOT_PATH.read_bytes()], True, environment, BUNDLE_ID, APP_ID)


def _verify(signed: str, method: str) -> tuple[Any, Environment]:
    if not configured():
        raise AppleConfigurationError("Apple subscriptions are not configured yet.")
    environments = [Environment.PRODUCTION]
    if os.getenv("STAGEVIVA_APPLE_ALLOW_SANDBOX", "false").lower() == "true":
        environments.append(Environment.SANDBOX)
    for environment in environments:
        try:
            return getattr(_verifier(environment), method)(signed), environment
        except VerificationException:
            continue
    raise AppleVerificationError("Apple could not verify this purchase.")


def _client(environment: Environment) -> AppStoreServerAPIClient:
    return AppStoreServerAPIClient(
        Path(os.environ["APPLE_IAP_PRIVATE_KEY_PATH"]).read_bytes(),
        os.environ["APPLE_IAP_KEY_ID"], os.environ["APPLE_IAP_ISSUER_ID"], BUNDLE_ID, environment,
    )


def _check_transaction(transaction: Any, expected_token: str | None) -> None:
    if transaction.productId != PRODUCT_ID or transaction.type != "Auto-Renewable Subscription":
        raise AppleVerificationError("This is not a StageViva Premium subscription.")
    if not transaction.originalTransactionId or not transaction.transactionId:
        raise AppleVerificationError("Apple transaction identifiers are missing.")
    token = str(transaction.appAccountToken or "").lower()
    if not token or (expected_token and token != expected_token.lower()):
        raise AppleVerificationError("This purchase belongs to a different StageViva account. Sign in to the account used to subscribe.")


def current_subscription(transaction: Any, environment: Environment, expected_token: str | None) -> dict[str, Any]:
    """Fetch latest state so a replayed, refunded purchase cannot grant access."""
    _check_transaction(transaction, expected_token)
    try:
        response = _client(environment).get_all_subscription_statuses(transaction.originalTransactionId)
    except Exception as error:
        raise AppleProviderError("Apple could not confirm your subscription. Please try restoring purchases shortly.") from error
    matches = []
    for group in response.data or []:
        for item in group.lastTransactions or []:
            if item.originalTransactionId != transaction.originalTransactionId:
                continue
            try:
                latest = _verifier(environment).verify_and_decode_signed_transaction(item.signedTransactionInfo)
                _check_transaction(latest, expected_token or str(transaction.appAccountToken))
                if latest.originalTransactionId != transaction.originalTransactionId:
                    raise AppleVerificationError("Apple returned a different subscription.")
                renewal = (_verifier(environment).verify_and_decode_renewal_info(item.signedRenewalInfo)
                           if item.signedRenewalInfo else None)
            except VerificationException as error:
                raise AppleVerificationError("Apple returned an invalid subscription signature.") from error
            expires = int(latest.expiresDate or 0) // 1000
            store_status = int(item.status)
            # Grace access only when Apple's signed renewal explicitly permits it.
            if store_status == 4 and renewal:
                expires = max(expires, int(renewal.gracePeriodExpiresDate or 0) // 1000)
            active = store_status in {1, 4} and expires > int(time.time()) and not latest.revocationDate and not latest.isUpgraded
            matches.append({
                "original_transaction_id": str(latest.originalTransactionId),
                "transaction_id": str(latest.transactionId),
                "account_token": str(latest.appAccountToken).lower(),
                "product_id": latest.productId, "environment": environment.value,
                "status": "active" if active else "expired" if store_status == 2 else "revoked" if store_status == 5 or latest.revocationDate else "inactive",
                "expires_at": expires, "signed_at": int(latest.signedDate or 0),
                "auto_renew": bool(renewal and renewal.autoRenewStatus == 1),
            })
    if not matches:
        raise AppleVerificationError("Apple did not return this subscription.")
    return max(matches, key=lambda item: item["signed_at"])


def verify_purchase(signed: str, user_id: str) -> dict[str, Any]:
    transaction, environment = _verify(signed, "verify_and_decode_signed_transaction")
    return current_subscription(transaction, environment, account_token(user_id))


def verify_notification(signed: str) -> tuple[str, dict[str, Any] | None]:
    notification, environment = _verify(signed, "verify_and_decode_notification")
    event_id = str(notification.notificationUUID or "")
    if not event_id:
        raise AppleVerificationError("Apple notification identifier is missing.")
    if notification.notificationType == "TEST":
        return event_id, None
    data = notification.data
    if not data or not data.signedTransactionInfo:
        return event_id, None
    try:
        transaction = _verifier(environment).verify_and_decode_signed_transaction(data.signedTransactionInfo)
    except VerificationException as error:
        raise AppleVerificationError("Apple notification transaction is invalid.") from error
    return event_id, current_subscription(transaction, environment, None)
