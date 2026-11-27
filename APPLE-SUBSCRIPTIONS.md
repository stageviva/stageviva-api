# StageViva Apple subscriptions

Product observed in App Store Connect on 27 November 2026:

- App: 6812465881, bundle com.stageviva.app
- Subscription group: StageViva Membership, 22413612
- Product: StageViva Premium, stageviva.premium.monthly, Apple ID 6816198694
- Duration: one month, auto-renewable. Existing UK web reference price £7.99/month.
- Last observed state: Prepare for Submission; 0/175 countries selected. Normal price not yet confirmed. Existing English UK display name/description and a review screenshot exist; screenshot must be checked against the replacement build.

## Server setup

Use an In-App Purchase API key from App Store Connect. An APNs key is not interchangeable. Keep the private .p8 file out of source, browser chat and logs. Put it in a Render Secret File and set:

```
APPLE_IAP_KEY_ID=<key identifier>
APPLE_IAP_ISSUER_ID=<issuer identifier>
APPLE_IAP_PRIVATE_KEY_PATH=/etc/secrets/<uploaded-file>.p8
STAGEVIVA_APPLE_PURCHASES_ENABLED=true
STAGEVIVA_APPLE_ALLOW_SANDBOX=true
```

Sandbox must be accepted during TestFlight and App Review. Only genuine signed Apple Sandbox transactions are accepted; Xcode/local/unsigned data are rejected. The server checks bundle, environment, product, account token and original transaction before delivery, then fetches the CURRENT subscription status from Apple to prevent replaying old refunded purchases. Grace access requires Apple's signed grace expiry. Requests fail closed if configuration, signature or current provider state cannot be verified.

Keep purchases disabled until keys, product, notifications and signed build are ready for sandbox tests. GET /me/billing/apple returns availability without returning keys. It does not prove StoreKit has loaded the product or that a real purchase passed.

Configure BOTH production and sandbox App Store Server Notifications URLs, V2:

```
https://api.stageviva.com/billing/apple/notifications
```

This matches the configured VITE_STAGEVIVA_API_URL. Request an Apple test notification and confirm successful delivery before release.

## App Store settings to finish

1. Confirm normal pricing (not Introductory/Promotional/Retention Offers), with the UK price £7.99/month; verify other storefront prices.
2. Select availability to match intended app markets. Current 0 countries prevents sale.
3. Keep Family Sharing off. Turn off streamlined purchasing and multiseat purchasing for this release: purchase linking requires the account token supplied by the signed-in app; business seats/tokenless purchases are not implemented.
4. Complete subscription group localization. Validate product localization, auto-renew duration and screenshot of the real replacement paywall. Do not create duplicate products.
5. Submit the first subscription with the replacement app version/build. Merely saving the product does not submit it.
6. Provide an App Review performer account whose Apple sandbox subscription has expired, with Basic access (no manual Premium grant), so reviewers can reach the Subscribe and Restore buttons. Enter credentials only in App Review Information, not source or chat. Record an actual sandbox expiry before claiming this is tested.

## Implementation and testing

- iOS only uses StoreKit via @capgo/native-purchases 8.10.0. Web Stripe and Android purchase behavior remain separate.
- Price comes from StoreKit, never a client-supplied or hardcoded entitlement. One monthly product for initial Apple launch.
- Server-owned pseudonymous appAccountToken binds transactions to the authenticated StageViva user. No account transfer or family entitlement inference.
- Transactions finish only after successful server verification/persistence. Failed deliveries stay recoverable via unfinished transactions, launch listener and Restore purchases.
- Apple notifications handle renewal/refund/expiry. Authenticated requests enforce stored Apple expiry even if an expiry notification was missed. Independent Stripe/School/manual access survives Apple expiry, and Apple access survives Stripe cancellation.
- Account deletion removes local Apple purchase metadata; it cannot cancel Apple billing. App warns users to cancel in App Store settings.

Required device tests before submitting: camera allow/deny/cancel/take photo on supported iPad/iPhone; native sign-in callback; real Sandbox product load, subscribe, restore/reinstall, expired subscription repurchase, cancellation, deferred purchase, renewal, refund/revocation, offline delivery/relaunch and existing web-paid account preventing duplicate subscription.

Local automated tests do not replace the signed build, Apple sandbox transactions or actual iPad camera test. No real charge should be made to test this release.
