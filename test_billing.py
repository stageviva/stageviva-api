from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import billing


class BillingSafetyTest(unittest.TestCase):
    def test_checkout_uses_only_the_configured_plan_price(self) -> None:
        configured = {
            "STAGEVIVA_STRIPE_MODE": "test",
            "STAGEVIVA_STRIPE_REQUESTS_ENABLED": "true",
            "STAGEVIVA_PUBLIC_APP_URL": "https://stageviva.com",
            "STRIPE_PREMIUM_MONTHLY_PRICE_ID": "price_monthly_safe",
            "STRIPE_AUDITION_SEASON_PRICE_ID": "price_season_safe",
        }
        with patch.dict(os.environ, configured, clear=False), patch.object(
            billing, "_stripe_api_request", return_value={"id": "cs_test", "url": "https://checkout.stripe.com/test"},
        ) as request:
            result = billing.create_checkout_session(
                user_id="user_safe", email="artist@example.com", plan="audition_season",
            )
        self.assertEqual(result["id"], "cs_test")
        path, fields = request.call_args.args
        self.assertEqual(path, "checkout/sessions")
        self.assertEqual(fields["line_items[0][price]"], "price_season_safe")
        self.assertEqual(fields["subscription_data[metadata][stageviva_user_id]"], "user_safe")
        self.assertNotIn("customer", fields)
        self.assertEqual(fields["customer_email"], "artist@example.com")

    def test_unknown_checkout_plan_is_rejected_before_any_request(self) -> None:
        with patch.object(billing, "_stripe_api_request") as request:
            with self.assertRaises(ValueError):
                billing.create_checkout_session(
                    user_id="user_safe", email="artist@example.com", plan="invented_plan",
                )
        request.assert_not_called()

    def test_stripe_network_is_disabled_by_default(self) -> None:
        with patch.dict(os.environ, {"STAGEVIVA_STRIPE_REQUESTS_ENABLED": "false"}, clear=False):
            with self.assertRaises(billing.BillingConfigurationError):
                billing._stripe_api_request("checkout/sessions", {})

    def test_live_key_cannot_be_used_while_stageviva_is_in_test_mode(self) -> None:
        configured = {
            "STAGEVIVA_STRIPE_MODE": "test",
            "STRIPE_SECRET_KEY": "sk_live_must_not_be_used",
        }
        with patch.dict(os.environ, configured, clear=False):
            with self.assertRaises(billing.BillingConfigurationError):
                billing._secret_key()


if __name__ == "__main__":
    unittest.main()
