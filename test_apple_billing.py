from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as Obj
from unittest.mock import Mock, patch

import apple_billing as apple
from appstoreserverlibrary.models.Environment import Environment
from storage import StageVivaStorage


def transaction(**changes):
    fields = dict(productId=apple.PRODUCT_ID, type="Auto-Renewable Subscription",
                  originalTransactionId="1001", transactionId="1002", appAccountToken=apple.account_token("one"),
                  expiresDate=(int(time.time()) + 3600) * 1000, signedDate=100,
                  revocationDate=None, isUpgraded=False)
    return Obj(**{**fields, **changes})


class AppleVerificationTest(unittest.TestCase):
    def current(self, *, latest=None, status=1, renewal=None):
        latest = latest or transaction()
        verifier = Mock()
        verifier.verify_and_decode_signed_transaction.return_value = latest
        verifier.verify_and_decode_renewal_info.return_value = renewal
        item = Obj(originalTransactionId="1001", signedTransactionInfo="signed", signedRenewalInfo="renewal" if renewal else None, status=status)
        client = Mock()
        client.get_all_subscription_statuses.return_value = Obj(data=[Obj(lastTransactions=[item])])
        with patch.object(apple, "_verifier", return_value=verifier), patch.object(apple, "_client", return_value=client):
            return apple.current_subscription(transaction(), Environment.PRODUCTION, apple.account_token("one"))

    def test_disabled_configuration_rejects_before_network(self):
        with patch.dict(os.environ, {"STAGEVIVA_APPLE_PURCHASES_ENABLED": "false"}), patch.object(apple, "_client") as client:
            with self.assertRaises(apple.AppleConfigurationError):
                apple.verify_purchase("fake", "one")
        client.assert_not_called()

    def test_unsigned_or_malformed_transaction_is_rejected(self):
        with patch.object(apple, "configured", return_value=True), patch.dict(os.environ, {"STAGEVIVA_APPLE_ALLOW_SANDBOX": "false"}):
            with self.assertRaises(apple.AppleVerificationError):
                apple.verify_purchase("not-an-apple-signed-transaction", "one")

    def test_wrong_account_is_rejected_before_server_lookup(self):
        with patch.object(apple, "_client") as client:
            with self.assertRaises(apple.AppleVerificationError):
                apple.current_subscription(transaction(), Environment.PRODUCTION, apple.account_token("two"))
        client.assert_not_called()

    def test_unknown_product_does_not_grant_access(self):
        with patch.object(apple, "_client") as client:
            with self.assertRaises(apple.AppleVerificationError):
                apple.current_subscription(transaction(productId="other"), Environment.PRODUCTION, apple.account_token("one"))
        client.assert_not_called()

    def test_active_server_confirmed_transaction_grants_access(self):
        self.assertEqual(self.current()["status"], "active")

    def test_replayed_active_transaction_does_not_override_current_refund(self):
        self.assertEqual(self.current(latest=transaction(revocationDate=1), status=5)["status"], "revoked")

    def test_expiry_is_enforced_even_if_status_claims_active(self):
        self.assertNotEqual(self.current(latest=transaction(expiresDate=1))["status"], "active")

    def test_billing_retry_without_grace_is_not_active(self):
        self.assertNotEqual(self.current(status=3)["status"], "active")

    def test_verified_grace_period_keeps_access_until_signed_date(self):
        renewal = Obj(gracePeriodExpiresDate=(int(time.time()) + 600) * 1000, autoRenewStatus=1)
        self.assertEqual(self.current(latest=transaction(expiresDate=1), status=4, renewal=renewal)["status"], "active")

    def test_latest_transaction_cannot_change_original_account(self):
        with self.assertRaises(apple.AppleVerificationError):
            self.current(latest=transaction(appAccountToken=apple.account_token("two")))

    def test_apple_outage_does_not_grant_client_claims(self):
        client = Mock()
        client.get_all_subscription_statuses.side_effect = TimeoutError()
        with patch.object(apple, "_client", return_value=client):
            with self.assertRaises(apple.AppleProviderError):
                apple.current_subscription(transaction(), Environment.PRODUCTION, apple.account_token("one"))


class AppleMembershipStorageTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.storage = StageVivaStorage(str(Path(self.directory.name) / "billing.db"))
        self.user = self.storage.create_user("one@example.test", "unused", "One")["id"]
        self.other = self.storage.create_user("two@example.test", "unused", "Two")["id"]
        self.details = dict(original_transaction_id="1001", transaction_id="1002",
                            account_token=apple.account_token(self.user), product_id=apple.PRODUCT_ID,
                            environment="Production", status="active", expires_at=int(time.time()) + 3600,
                            signed_at=100, auto_renew=True)

    def tearDown(self):
        self.storage.close()
        self.directory.cleanup()

    def test_delivery_idempotency_and_no_transfer_to_another_user(self):
        self.storage.save_apple_subscription(self.user, self.details)
        self.storage.save_apple_subscription(self.user, self.details)
        self.assertEqual(len(self.storage.apple_subscriptions_for_user(self.user)), 1)
        self.assertEqual(self.storage.get_user(self.user)["membership_tier"], "pro")
        with self.assertRaises(ValueError):
            self.storage.save_apple_subscription(self.other, self.details)
        self.assertEqual(self.storage.get_user(self.other)["membership_tier"], "free")

    def test_expiry_removes_only_apple_access_without_a_notification(self):
        self.storage.save_apple_subscription(self.user, self.details)
        with patch("storage.time.time", return_value=self.details["expires_at"] + 1):
            self.assertEqual(self.storage.reconcile_paid_membership(self.user)["membership_tier"], "free")

    def test_manual_grant_survives_apple_purchase_and_expiry(self):
        self.storage.update_membership_tier(self.user, "pro")
        self.storage.save_apple_subscription(self.user, self.details)
        with patch("storage.time.time", return_value=self.details["expires_at"] + 1):
            self.assertEqual(self.storage.reconcile_paid_membership(self.user)["membership_tier"], "pro")
            self.assertEqual(self.storage.get_user(self.user)["membership_source"], "manual")

    def test_stripe_expiry_does_not_revoke_active_apple_subscription(self):
        self.storage.save_apple_subscription(self.user, self.details)
        self.storage.upsert_billing_subscription(self.user, provider_customer_id="cus", provider_subscription_id="sub", plan="premium_monthly", price_id="p", status="canceled", current_period_end=1, cancel_at_period_end=True)
        self.storage.update_stripe_membership(self.user, entitled=False)
        self.assertEqual(self.storage.get_user(self.user)["membership_source"], "apple")

    def test_unrecognised_stripe_price_cannot_gain_access_during_reconciliation(self):
        self.storage.upsert_billing_subscription(self.user, provider_customer_id="cus", provider_subscription_id="sub", plan="", price_id="unknown", status="active", current_period_end=None, cancel_at_period_end=False)
        self.storage.update_stripe_membership(self.user, entitled=False)
        self.assertEqual(self.storage.reconcile_paid_membership(self.user)["membership_tier"], "free")

    def test_old_event_does_not_replace_a_newer_refund(self):
        self.storage.save_apple_subscription(self.user, {**self.details, "signed_at": 200, "status": "revoked"})
        self.storage.save_apple_subscription(self.user, self.details)
        self.assertEqual(self.storage.get_user(self.user)["membership_tier"], "free")

    def test_legacy_stripe_migration_preserves_paid_access_without_promoting_unknown_prices(self):
        for user_id, plan in [(self.user, "premium_monthly"), (self.other, "")]:
            self.storage.upsert_billing_subscription(user_id, provider_customer_id=f"cus_{user_id}", provider_subscription_id=f"sub_{user_id}", plan=plan, price_id="existing", status="active", current_period_end=None, cancel_at_period_end=False)
        self.storage.update_membership_tier(self.user, "pro", source="stripe")
        self.storage.connection.execute("ALTER TABLE billing_subscriptions DROP COLUMN entitled")
        self.storage.connection.commit()
        self.storage._create_schema()
        self.assertEqual(self.storage.reconcile_paid_membership(self.user)["membership_tier"], "pro")
        self.assertEqual(self.storage.reconcile_paid_membership(self.other)["membership_tier"], "free")

    def test_account_deletion_clears_apple_data(self):
        self.storage.register_apple_account(self.user, apple.account_token(self.user))
        self.storage.save_apple_subscription(self.user, self.details)
        self.storage.delete_user_account(self.user)
        self.assertEqual(self.storage.apple_subscriptions_for_user(self.user), [])
        self.assertIsNone(self.storage.apple_user_for_token(apple.account_token(self.user)))


class AppleApiTest(unittest.TestCase):
    def setUp(self):
        import api
        from fastapi.testclient import TestClient
        self.api = api
        self.directory = tempfile.TemporaryDirectory()
        self.storage = StageVivaStorage(str(Path(self.directory.name) / "api.db"))
        self.previous_overrides = dict(api.app.dependency_overrides)
        api.app.dependency_overrides[api.get_storage] = lambda: self.storage
        self.local_auth = patch.object(api, "_lovable_auth_enabled", return_value=False)
        self.local_auth.start()
        self.client = TestClient(api.app)
        response = self.client.post("/auth/register", json={"email": "apple-api@example.com", "password": "test-only-password", "display_name": "Apple Test"})
        self.assertEqual(response.status_code, 201, response.text)
        self.user_id = response.json()["user"]["id"]
        self.headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
        self.details = dict(original_transaction_id="1001", transaction_id="1002", account_token=apple.account_token(self.user_id), product_id=apple.PRODUCT_ID, environment="Sandbox", status="active", expires_at=int(time.time()) + 3600, signed_at=100, auto_renew=True)

    def tearDown(self):
        self.api.app.dependency_overrides = self.previous_overrides
        self.local_auth.stop()
        self.storage.close()
        self.directory.cleanup()

    def test_purchase_requires_authenticated_account(self):
        with patch.object(apple, "verify_purchase") as verify:
            response = self.client.post("/billing/apple/verify", json={"signed_transaction": "x" * 30})
        self.assertEqual(response.status_code, 401)
        verify.assert_not_called()

    def test_rejected_purchase_does_not_change_access(self):
        with patch.object(apple, "verify_purchase", side_effect=apple.AppleVerificationError("Invalid purchase")):
            response = self.client.post("/billing/apple/verify", headers=self.headers, json={"signed_transaction": "x" * 30})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.storage.get_user(self.user_id)["membership_tier"], "free")

    def test_verified_purchase_and_restore_are_idempotent_and_block_duplicate_web_checkout(self):
        with patch.object(apple, "verify_purchase", return_value=self.details) as verify:
            for _ in range(2):
                response = self.client.post("/billing/apple/verify", headers=self.headers, json={"signed_transaction": "x" * 30})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.json()["premium"])
            self.assertEqual(verify.call_args.args[1], self.user_id)
        self.assertEqual(len(self.storage.apple_subscriptions_for_user(self.user_id)), 1)
        self.assertEqual(self.client.post("/billing/checkout", headers=self.headers, json={"plan": "premium_monthly"}).status_code, 409)

    def test_verified_refund_notification_revokes_access_and_is_idempotent(self):
        self.storage.register_apple_account(self.user_id, self.details["account_token"])
        self.storage.save_apple_subscription(self.user_id, self.details)
        with patch.object(apple, "verify_notification", return_value=("event-1", {**self.details, "status": "revoked", "signed_at": 200})):
            for _ in range(2):
                response = self.client.post("/billing/apple/notifications", json={"signedPayload": "x" * 30})
                self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.storage.get_user(self.user_id)["membership_tier"], "free")
        self.assertTrue(self.storage.billing_event_processed("apple:event-1"))

    def test_expiry_is_enforced_on_authenticated_requests_without_notification(self):
        self.storage.save_apple_subscription(self.user_id, self.details)
        with patch("storage.time.time", return_value=self.details["expires_at"] + 1):
            response = self.client.get("/me", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["membership_tier"], "free")


if __name__ == "__main__":
    unittest.main()
