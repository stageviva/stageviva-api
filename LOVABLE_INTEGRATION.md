# StageViva backend → Lovable

## Run locally

```powershell
.\.venv\Scripts\uvicorn.exe api:app --reload
```

The API is then available at `http://localhost:8000`; interactive documentation is at `/docs` and the OpenAPI contract is at `/openapi.json`.

## Required production configuration

Copy `.env.example` to `.env`, then set:

- `OPENAI_API_KEY` for CV and opportunity analysis.
- `STAGEVIVA_AUTH_JWKS_URL` to Lovable Cloud's published JWKS URL, normally
  `https://<your-supabase-project>.supabase.co/auth/v1/.well-known/jwks.json`.
- `STAGEVIVA_AUTH_ISSUER` to `https://<your-supabase-project>.supabase.co/auth/v1`
  and `STAGEVIVA_AUTH_AUDIENCE` to `authenticated` when those values are used by
  your Lovable Cloud project.
- `STAGEVIVA_SUPABASE_PUBLISHABLE_KEY` when Lovable Cloud issues legacy HS256
  session tokens. This is the public `sb_publishable_...` key, never a secret
  key; StageViva validates those sessions directly with Supabase Auth.
- `STAGEVIVA_CORS_ORIGINS` to the Lovable production URL, for example `https://your-project.lovable.app`.
- `RESEND_API_KEY` and `RESEND_FROM_EMAIL` only after the sender domain is verified.

The included `Dockerfile` runs the service on port `8000`. Attach persistent storage to `/app/data` so `stageviva.db` survives deploys.

## Lovable API contract

All protected endpoints require `Authorization: Bearer <access_token>`. In production,
the token is the existing Lovable Cloud session token. The backend verifies it against
Lovable's JWKS and creates or links the internal StageViva performer profile on first use.

| Action | Endpoint |
| --- | --- |
| Health check | `GET /health` |
| Source coverage and daily run status | `GET /sources` |
| Local development account | `POST /auth/register`, `POST /auth/login` |
| Current user | `GET /me` |
| Update performer profile | `PUT /me/profile` |
| Start CV analysis | `POST /me/cv` multipart field `cv` |
| Read CV analysis status | `GET /me/cv-analysis` |
| Read Artist DNA | `GET /me/artist-dna` |
| Read matching-profile questions | `GET /me/profile-questions` |
| Save Artist DNA | `PUT /me/artist-dna` |
| Answer AI follow-up questions | `POST /me/artist-dna/answers` |
| Matches and opportunity cards | `GET /matches` or `GET /opportunities` |
| In-app notifications | `GET /notifications` |
| Mark notification read | `POST /notifications/{id}/read` |
| Notification preferences | `PUT /me/notification-preferences` |

`POST /me/cv` accepts PDF, DOCX, or TXT files up to 10 MB and immediately returns
`202` with `status: processing`. Poll `GET /me/cv-analysis` until `complete`;
the completed response includes `artist_dna`. This avoids browser timeouts while
Artist DNA, matches, and qualifying notifications are generated.

## Background commands

Discover, analyse, persist and match a source against every registered user:

```powershell
.\.venv\Scripts\python.exe stageviva_pipeline.py --source BalletPlaces --database stageviva.db --limit 3
```

Run every validated source once. This imports every newly discovered listing from each validated source; pass `--limit-per-source 20` only when you deliberately want a smaller manual test. Configure this command as a daily cron job in the deployment provider; source runs are recorded and visible through `GET /sources`.

```powershell
.\.venv\Scripts\python.exe daily_pipeline.py --database stageviva.db --limit-per-source 20
```

Only sources marked `automation_ready` are included. BalletPlaces is the first validated source; each remaining source needs its own adapter and live validation before it is enabled.

Preview email notifications without sending anything:

```powershell
.\.venv\Scripts\python.exe send_notifications.py --database stageviva.db --dry-run
```

After the Resend variables are configured, run the same command without `--dry-run` to send queued emails.
