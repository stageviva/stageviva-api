# StageViva: App Store Privacy Checklist

Use this when completing **App Store Connect > App Privacy** for the first iOS build. It reflects the current StageViva service and must be rechecked if a native wrapper adds analytics, payments, crash reporting, advertising or new SDKs.

## Overall answers

- **Does the app collect data?** Yes.
- **Is any collected data used to track users across apps or websites?** No.
- **Is data used for third-party advertising or sold?** No.

## Data to declare now

| Apple data type | Is it linked to identity? | Purpose | Notes |
| --- | --- | --- | --- |
| Contact Info: Name | Yes | App functionality | Account and performer profile. |
| Contact Info: Email Address | Yes | App functionality | Sign-in, account support and optional alerts. |
| Identifiers: User ID | Yes | App functionality | Internal/authentication account identifier. |
| User Content: Other User Content | Yes | App functionality | CV file/text, Artist DNA, profile answers, professional experience, preferences and matching information. |
| Photos | Yes | App functionality | Only if the iOS app stores or displays an extracted/uploaded profile headshot. |
| Other Data | Yes | App functionality | Membership status, match scores and notification preferences, if App Store Connect requires these to be declared separately. |

## Confirm at native-app build time

- If the iOS build registers an Apple Push Notification service (APNs) device token, declare the relevant **Identifiers / Device ID** or **Other Data** entry for **App Functionality** as instructed by App Store Connect.
- Do not declare precise device location, contacts, health/fitness, financial information, browsing history, advertising data or diagnostics unless an actual SDK or feature collects them.
- If you add Firebase, Sentry, revenue/paywall tooling, analytics, Apple/Google social sign-in, subscription payments or any other SDK, revisit this checklist before submission.

## Product links that must exist before submission

- Privacy policy: `https://stageviva.com/privacy`
- Support page: `https://stageviva.com/support`
- Support email visible on the support page: `stageviva@gmail.com`

## Required decisions before publishing the policy

1. Replace the legal operator name and correspondence address in `PRIVACY_POLICY_DRAFT.md`.
2. Choose StageViva's minimum age (recommended: 16, if that fits the intended audience).
3. Confirm the current backup-retention period is 14 days. Change the policy if the live configuration differs.
4. Confirm whether Lovable Cloud / Supabase is the live authentication provider and whether Resend is enabled for email notifications.
5. Have a UK privacy professional review the final legal text before a public consumer launch.
