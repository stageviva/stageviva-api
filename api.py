"""Lovable-ready REST API for StageViva."""

from __future__ import annotations

import os
import secrets
import hmac
import logging
import json
import threading
from datetime import date, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, Annotated
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import jwt
from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, Query, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field
from pwdlib import PasswordHash

from artist_intelligence import analyse_artist, enrich_artist_dna
from cv_headshot import extract_cv_headshot
from catalogue_seed import seed_catalogue_if_empty
from database_backups import create_database_backup, list_database_backups
from match_service import match_artist_to_opportunity
from opportunity_discovery import DiscoveredOpportunity
from opportunity_policy import is_stageviva_eligible
from opportunity_lifecycle import is_current_opportunity
from opportunity_presentation import matches_for_filters, opportunity_detail
from push_notifications import deliver_pending_push_notifications, public_vapid_key, send_test_push_notification
from source_registry import get_active_sources
from storage import StageVivaStorage

load_dotenv()

def _persistent_runtime_path(filename: str, environment_key: str, local_default: str) -> str:
    """Prefer Render's attached disk whenever it is available.

    Frontend deployments must never be able to leave the API writing profiles
    or CVs to the service's disposable working directory. Render mounts the
    production disk at ``/var/data``; local development continues to honour
    the configured path or the ordinary project-relative default.
    """
    disk_directory = Path("/var/data")
    if disk_directory.is_dir():
        return str(disk_directory / filename)
    return os.getenv(environment_key, local_default)


DATABASE_PATH = _persistent_runtime_path("stageviva.db", "STAGEVIVA_DB", "stageviva.db")
UPLOADS_DIR = Path(_persistent_runtime_path("uploads", "STAGEVIVA_UPLOADS_DIR", "data/uploads"))
TOKEN_LIFETIME_SECONDS = 60 * 60 * 24 * 14
password_hasher = PasswordHash.recommended()
bearer_scheme = HTTPBearer(auto_error=False)
logger = logging.getLogger("stageviva.auth")
SUPPORTED_EXTERNAL_JWT_ALGORITHMS = frozenset({
    "RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "EdDSA",
})
MEMBERSHIP_TIERS = frozenset({"beta", "free", "pro", "school"})
source_scan_lock = threading.Lock()


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    display_name: str = Field(min_length=1, max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ProfileRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    profile: dict[str, Any] = Field(default_factory=dict)


class NotificationPreferencesRequest(BaseModel):
    email_notifications: bool
    in_app_notifications: bool


class PushSubscriptionRequest(BaseModel):
    endpoint: str = Field(min_length=1, max_length=4096)
    keys: dict[str, str]


class AdminOpportunityUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    organisation: str | None = Field(default=None, min_length=1, max_length=240)
    role_summary: str | None = Field(default=None, min_length=1, max_length=500)
    location: str | None = Field(default=None, min_length=1, max_length=240)
    deadline: str | None = Field(default=None, min_length=1, max_length=120)
    official_url: str | None = Field(default=None, min_length=8, max_length=2048)
    contract_type: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = Field(default=None, max_length=3000)
    visible: bool | None = None


class AdminManualOpportunityRequest(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    organisation: str = Field(min_length=1, max_length=240)
    role_summary: str = Field(min_length=1, max_length=500)
    location: str = Field(min_length=1, max_length=240)
    deadline: str = Field(min_length=1, max_length=120)
    official_url: str = Field(min_length=8, max_length=2048)
    contract_type: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=3000)


class AdminMembershipUpdateRequest(BaseModel):
    """Creator-controlled complimentary access during the beta."""
    membership_tier: str = Field(pattern="^(free|beta|pro|school)$")


class ArtistDNARequest(BaseModel):
    artist_dna: dict[str, Any]


class ArtistDNAAnswersRequest(BaseModel):
    answers: list[dict[str, str]] = Field(min_length=1, max_length=20)


class ArtistDNAUpdateRequest(BaseModel):
    """A safe partial update from the performer-facing profile editor."""
    updates: dict[str, Any] = Field(default_factory=dict)


def get_storage():
    storage = StageVivaStorage(DATABASE_PATH)
    try:
        yield storage
    finally:
        storage.close()


Storage = Annotated[StageVivaStorage, Depends(get_storage)]


def _token_secret(storage: StageVivaStorage) -> str:
    configured = os.getenv("STAGEVIVA_AUTH_SECRET")
    if configured:
        return configured
    return storage.get_or_create_setting("auth_secret", lambda: secrets.token_urlsafe(48))


def _public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user["id"], "email": user["email"], "display_name": user["display_name"],
        "profile": user["profile"], "artist_id": user["artist_id"],
        "membership_tier": _membership_tier(user),
        "is_admin": _is_admin_email(user["email"]),
        "notification_preferences": {
            "email_notifications": user["email_notifications"],
            "in_app_notifications": user["in_app_notifications"],
        },
        "created_at": user["created_at"],
    }


def _membership_tier(user: dict[str, Any]) -> str:
    tier = str(user.get("membership_tier") or "beta").lower()
    return tier if tier in MEMBERSHIP_TIERS else "free"


def _access_for_user(user: dict[str, Any]) -> dict[str, Any]:
    """One stable contract for the Free/Pro UI and future payment providers.

    Beta is deliberately equivalent to Pro while we validate the product with
    real performers.  It can be removed without changing the frontend once
    paid plans are switched on.
    """
    tier = _membership_tier(user)
    immediate_access = tier in {"beta", "pro", "school"}
    return {
        "membership_tier": tier,
        "is_beta": tier == "beta",
        "opportunities": {
            "release": "immediate" if immediate_access else "weekly_monday",
            "show_full_details": immediate_access,
            "show_company_or_role_before_release": immediate_access,
            "show_apply_link_before_release": immediate_access,
        },
        "matches": {
            "realtime": immediate_access,
            "full_list": immediate_access,
            "explain_match_gaps": immediate_access,
        },
        "cv": {
            "reanalyses": "reasonable_use" if immediate_access else "one_per_30_days",
            "improvement_plan": immediate_access,
        },
    }


def _create_token(user_id: str, storage: StageVivaStorage) -> str:
    import time
    return jwt.encode(
        {"sub": user_id, "iat": int(time.time()), "exp": int(time.time()) + TOKEN_LIFETIME_SECONDS},
        _token_secret(storage), algorithm="HS256",
    )


def _lovable_auth_enabled() -> bool:
    """External auth is opt-in so the API remains runnable locally without Lovable."""
    return bool(os.getenv("STAGEVIVA_AUTH_JWKS_URL"))


@lru_cache(maxsize=4)
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=3600)


def _verify_lovable_token(token: str) -> dict[str, Any]:
    """Verify a Lovable Cloud/Supabase access token using its published JWKS."""
    jwks_url = os.getenv("STAGEVIVA_AUTH_JWKS_URL")
    if not jwks_url:
        raise jwt.InvalidTokenError("External authentication is not configured.")
    header = jwt.get_unverified_header(token)
    algorithm = header.get("alg")
    if algorithm == "HS256":
        return _verify_hs256_token_with_supabase(token)
    if algorithm not in SUPPORTED_EXTERNAL_JWT_ALGORITHMS:
        raise jwt.InvalidTokenError("Unsupported token algorithm.")
    signing_key = _jwks_client(jwks_url).get_signing_key_from_jwt(token)
    options: dict[str, Any] = {"algorithms": [algorithm]}
    issuer = os.getenv("STAGEVIVA_AUTH_ISSUER")
    audience = os.getenv("STAGEVIVA_AUTH_AUDIENCE")
    if issuer:
        options["issuer"] = issuer
    if audience:
        options["audience"] = audience
    else:
        options["options"] = {"verify_aud": False}
    return jwt.decode(token, signing_key.key, **options)


def _verify_hs256_token_with_supabase(token: str) -> dict[str, Any]:
    """Validate legacy HS256 sessions without copying Supabase's JWT secret."""
    publishable_key = os.getenv("STAGEVIVA_SUPABASE_PUBLISHABLE_KEY")
    issuer = os.getenv("STAGEVIVA_AUTH_ISSUER", "").rstrip("/")
    if not publishable_key or not issuer:
        raise jwt.InvalidTokenError("HS256 verification is not configured.")
    request = Request(
        f"{issuer}/user",
        headers={"Authorization": f"Bearer {token}", "apikey": publishable_key},
        method="GET",
    )
    try:
        with urlopen(request, timeout=10) as response:  # nosec B310 - configured Supabase origin
            user = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise jwt.InvalidTokenError("Supabase rejected the access token.") from error
    if not isinstance(user, dict) or not isinstance(user.get("id"), str):
        raise jwt.InvalidTokenError("Supabase returned an invalid user response.")
    return {**user, "sub": user["id"]}


def _external_display_name(payload: dict[str, Any]) -> str | None:
    metadata = payload.get("user_metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    for value in (metadata.get("full_name"), metadata.get("name"), payload.get("email")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    storage: Storage,
) -> dict[str, Any]:
    if not credentials:
        logger.warning("Authentication rejected: no bearer token was supplied.")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    try:
        if _lovable_auth_enabled():
            payload = _verify_lovable_token(credentials.credentials)
            subject = str(payload["sub"])
            user = storage.get_or_create_external_user(
                subject,
                payload.get("email") if isinstance(payload.get("email"), str) else None,
                _external_display_name(payload),
            )
        else:
            payload = jwt.decode(credentials.credentials, _token_secret(storage), algorithms=["HS256"])
            user = storage.get_user(str(payload["sub"]))
    except (jwt.PyJWTError, jwt.PyJWKClientError, KeyError) as error:
        logger.warning("Authentication rejected: %s", error)
        user = None
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token.")
    return user


CurrentUser = Annotated[dict[str, Any], Depends(current_user)]


def _is_admin_email(email: str) -> bool:
    """Check the owner allow-list configured in Render without exposing it."""
    allowed = {
        email.strip().casefold()
        for email in os.getenv("STAGEVIVA_ADMIN_EMAILS", "").split(",")
        if email.strip()
    }
    return email.casefold() in allowed


def require_admin(user: CurrentUser) -> dict[str, Any]:
    """Restrict moderation to the owner emails configured in Render."""
    if not _is_admin_email(user["email"]):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access required.")
    return user


AdminUser = Annotated[dict[str, Any], Depends(require_admin)]


@asynccontextmanager
async def lifespan(_: FastAPI):
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    # If the service is restarted mid-analysis, resume from the securely saved
    # CV instead of leaving the performer on a permanent loading screen.
    storage = StageVivaStorage(DATABASE_PATH)
    try:
        seeded = seed_catalogue_if_empty(storage)
        if seeded:
            logger.info("Seeded %s validated opportunities for a new database", seeded)
        pending_users = storage.list_processing_cv_analyses()
        artists_to_refresh = storage.list_registered_artists() if seeded else []
    finally:
        storage.close()
    for user_id in pending_users:
        user_directory = UPLOADS_DIR / user_id
        files = sorted(user_directory.glob("cv.*"), key=lambda path: path.stat().st_mtime, reverse=True)
        if files:
            threading.Thread(
                target=_analyse_uploaded_cv_in_background,
                args=(user_id, str(files[0])), daemon=True,
            ).start()
    # Headshot extraction was introduced after some beta CVs had already been
    # analysed. Backfill those private uploads once, without asking anyone to
    # spend one of their two CV uploads again.
    for user_directory in UPLOADS_DIR.iterdir():
        if user_directory.is_dir():
            threading.Thread(
                target=_backfill_cv_headshot,
                args=(user_directory.name,), daemon=True,
            ).start()
    # A performer may upload their CV just before the first catalogue is
    # available. Refresh those stored profiles after seeding so they are not
    # asked to upload the same CV again.
    for artist_id, artist_dna in artists_to_refresh:
        threading.Thread(
            target=_refresh_existing_artist_matches,
            args=(artist_id, artist_dna), daemon=True,
        ).start()
    yield


app = FastAPI(title="StageViva API", version="0.1.0", lifespan=lifespan)
origins = [origin.strip() for origin in os.getenv(
    "STAGEVIVA_CORS_ORIGINS", "http://localhost:5173,http://localhost:3000",
).split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware, allow_origins=origins, allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "OPTIONS"],
    # Lovable Cloud adds its Supabase client-identification headers to browser
    # requests.  They are harmless here (authentication still depends on the
    # verified bearer JWT), but CORS must allow them before a CV reaches us.
    allow_headers=["Authorization", "Content-Type", "Accept", "apikey", "x-client-info"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "stageviva-api"}


@app.get("/sources")
def source_status(storage: Storage) -> dict[str, Any]:
    return {
        "sources": [{
            "name": source.name, "category": source.category, "priority": source.priority,
            "active": source.active, "automation_ready": source.automation_ready,
        } for source in get_active_sources()],
        "recent_runs": storage.list_source_runs(),
    }


@app.post("/auth/register", status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, storage: Storage) -> dict[str, Any]:
    try:
        user = storage.create_user(payload.email, password_hasher.hash(payload.password), payload.display_name)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return {"access_token": _create_token(user["id"], storage), "token_type": "bearer", "user": _public_user(user)}


@app.post("/auth/login")
def login(payload: LoginRequest, storage: Storage) -> dict[str, Any]:
    user = storage.get_user_by_email(payload.email)
    if not user or not password_hasher.verify(payload.password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password.")
    return {"access_token": _create_token(user["id"], storage), "token_type": "bearer", "user": _public_user(user)}


@app.get("/me")
def me(user: CurrentUser) -> dict[str, Any]:
    return _public_user(user)


@app.get("/me/access")
def get_my_access(user: CurrentUser) -> dict[str, Any]:
    """Return feature access without exposing billing-provider implementation details."""
    return _access_for_user(user)


@app.put("/me/profile")
def update_profile(payload: ProfileRequest, user: CurrentUser, storage: Storage) -> dict[str, Any]:
    saved_user = storage.update_user_profile(user["id"], payload.display_name, payload.profile)
    # Keep a performer-confirmed gender from the compact profile editor in
    # their matching DNA too. This supports both the current basic editor and
    # the full Artist DNA editor without asking the performer twice.
    gender = _normalise_gender(
        payload.profile.get("gender", payload.profile.get("gender_identity"))
    )
    artist = storage.get_artist_for_user(user["id"])
    if artist and gender != "unknown":
        existing = _normalise_gender(artist["dna"].get("identity", {}).get("gender"))
        if gender != existing:
            updated_dna = _deep_merge(artist["dna"], {"identity": {"gender": gender}})
            _store_and_match_artist(user["id"], updated_dna, storage)
    return _public_user(saved_user)


@app.get("/me/artist-dna")
def get_artist_dna(user: CurrentUser, storage: Storage) -> dict[str, Any]:
    artist = storage.get_artist_for_user(user["id"])
    if not artist:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No Artist DNA yet.")
    artist["dna"] = _artist_dna_with_profile_questions(artist["dna"])
    return artist


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip() or value.strip().lower() in {"unknown", "not specified", "n/a"}
    return not bool(value)


def _age_from_date_of_birth(value: Any, today: date | None = None) -> str | None:
    """Calculate age from a confirmed date rather than asking AI to infer it."""
    raw = str(value or "").strip()
    if not raw:
        return None
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            born = datetime.strptime(raw, pattern).date()
            current = today or date.today()
            return str(current.year - born.year - ((current.month, current.day) < (born.month, born.day)))
        except ValueError:
            continue
    return None


def _synchronise_date_of_birth(identity: dict[str, Any]) -> None:
    calculated_age = _age_from_date_of_birth(identity.get("date_of_birth"))
    if calculated_age is not None:
        identity["age"] = calculated_age


_GENDER_ALIASES = {
    "male": "male", "man": "male", "men": "male", "m": "male",
    "female": "female", "woman": "female", "women": "female", "f": "female",
    "non-binary": "non_binary", "nonbinary": "non_binary", "non binary": "non_binary",
    "prefer not to say": "unknown", "unknown": "unknown", "": "unknown",
}


def _normalise_gender(value: Any) -> str:
    """Normalise a performer-confirmed gender without making assumptions."""
    return _GENDER_ALIASES.get(str(value or "").strip().lower(), "unknown")


def _matching_profile_questions(artist_dna: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only short, actionable questions that influence matching."""
    identity = artist_dna.get("identity", {})
    eligibility = artist_dna.get("eligibility", {})
    preferences = artist_dna.get("preferences", {})
    questions: list[dict[str, Any]] = []
    if _is_missing(identity.get("date_of_birth")):
        questions.append({"id": "date_of_birth", "question": "What is your date of birth?", "type": "date"})
    if _is_missing(identity.get("gender")):
        questions.append({
            "id": "gender", "question": "Which gender should we use when matching gender-specific roles?",
            "type": "single_select", "options": ["Male", "Female", "Non-binary", "Prefer not to say"],
        })
    if _is_missing(identity.get("nationality")):
        questions.append({"id": "nationality", "question": "What is your nationality?", "type": "text"})
    if _is_missing(identity.get("location")):
        questions.append({"id": "current_location", "question": "Where are you currently based?", "type": "text"})
    if _is_missing(eligibility.get("work_rights")) and _is_missing(eligibility.get("visa_status")):
        questions.append({
            "id": "work_rights", "question": "Do you currently have the right to work where you are based?",
            "type": "single_select", "options": ["Yes", "No", "I need visa sponsorship", "Not sure"],
        })
    if _is_missing(preferences.get("availability")):
        questions.append({
            "id": "availability", "question": "When are you next available for a new contract?",
            "type": "single_select", "options": ["Available now", "Within 1 month", "Within 3 months", "Choose a date"],
        })
    if _is_missing(preferences.get("relocation_preferences")):
        questions.append({
            "id": "relocation", "question": "Would you be willing to relocate for the right opportunity?",
            "type": "single_select", "options": ["Yes, internationally", "Yes, within my region", "No", "Depends on the contract"],
        })
    if _is_missing(preferences.get("contract_preferences")):
        questions.append({
            "id": "contract_preferences", "question": "What kinds of opportunity are you looking for?",
            "type": "multi_select", "options": ["Paid company contract", "Freelance/project work", "Apprenticeship or trainee role", "Cruise/resort contract", "Commercial or screen work"],
        })
    return questions


def _artist_dna_with_profile_questions(artist_dna: dict[str, Any]) -> dict[str, Any]:
    """Expose the practical matching questions with every Artist DNA response.

    The CV model identifies gaps, while this layer consistently turns the
    important gaps into the short setup questions the product can ask.
    """
    enriched = json.loads(json.dumps(artist_dna))
    intelligence = enriched.setdefault("intelligence", {})
    generated = _matching_profile_questions(enriched)
    intelligence["questions_to_ask"] = [item["question"] for item in generated]
    intelligence["profile_questions"] = generated
    return enriched


@app.get("/me/profile-questions")
def get_profile_questions(user: CurrentUser, storage: Storage) -> dict[str, Any]:
    artist = storage.get_artist_for_user(user["id"])
    if not artist:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No Artist DNA yet.")
    return {"questions": _matching_profile_questions(artist["dna"])}


def _match_artist_against_catalogue(
    artist_id: str, artist_dna: dict[str, Any], storage: StageVivaStorage,
) -> dict[str, int]:
    matched = queued = 0
    opportunities = storage.list_opportunity_dnas()

    # OpenAI matching is network-bound. Evaluate a small group concurrently,
    # then keep database writes serial so SQLite remains reliable.
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(opportunities)))) as executor:
        futures = {
            executor.submit(match_artist_to_opportunity, artist_dna, opportunity): opportunity_id
            for opportunity_id, opportunity in opportunities
        }
        for future in as_completed(futures):
            opportunity_id = futures[future]
            try:
                results[opportunity_id] = future.result()
            except Exception:
                logger.exception("Matching failed for opportunity %s", opportunity_id)

    for opportunity_id, _ in opportunities:
        result = results.get(opportunity_id)
        if result is None:
            continue
        storage.upsert_match(artist_id, opportunity_id, result)
        matched += 1
        if result.get("overall", {}).get("recommendation") in {"strong_match", "good_match"}:
            queued += int(storage.queue_notification(artist_id, opportunity_id, result))
    return {"matched_opportunities": matched, "queued_notifications": queued}


def _match_opportunity_against_registered_artists(
    opportunity_id: str, opportunity: dict[str, Any], storage: StageVivaStorage,
) -> dict[str, int]:
    """Refresh all scores after an owner adds or materially corrects a listing."""
    matched = queued = 0
    for artist_id, artist_dna in storage.list_registered_artists():
        result = match_artist_to_opportunity(artist_dna, opportunity)
        storage.upsert_match(artist_id, opportunity_id, result)
        matched += 1
        if result.get("overall", {}).get("recommendation") in {"strong_match", "good_match"}:
            queued += int(storage.queue_notification(artist_id, opportunity_id, result))
    return {"matched_artists": matched, "queued_notifications": queued}


def _store_and_match_artist(user_id: str, artist_dna: dict[str, Any], storage: StageVivaStorage) -> dict[str, int]:
    artist_id = storage.upsert_artist_for_user(user_id, artist_dna)
    return _match_artist_against_catalogue(artist_id, artist_dna, storage)


def _refresh_existing_artist_matches(artist_id: str, artist_dna: dict[str, Any]) -> None:
    """Populate matches when a fresh database receives its first catalogue."""
    storage = StageVivaStorage(DATABASE_PATH)
    try:
        _match_artist_against_catalogue(artist_id, artist_dna, storage)
    except Exception:
        logger.exception("Catalogue refresh failed for artist %s", artist_id)
    finally:
        storage.close()


def _run_daily_source_scan() -> None:
    """Run the live discovery pipeline on the web service's persistent data."""
    try:
        # Imported lazily to keep normal API boot lightweight.
        from daily_pipeline import run_daily_pipeline

        limit = int(os.getenv("STAGEVIVA_DAILY_SOURCE_LIMIT", "5"))
        backup = create_database_backup(
            DATABASE_PATH, keep=max(1, int(os.getenv("STAGEVIVA_BACKUP_RETENTION_DAYS", "14"))),
        )
        logger.info("Daily database backup completed: %s", backup)
        storage = StageVivaStorage(DATABASE_PATH)
        try:
            result = run_daily_pipeline(storage, limit_per_source=max(1, limit))
            logger.info("Daily source scan completed: %s", result)
            if os.getenv("RESEND_API_KEY") and os.getenv("RESEND_FROM_EMAIL"):
                from email_notifications import deliver_pending_emails

                delivery = deliver_pending_emails(storage)
                logger.info("Notification email delivery completed: %s", delivery)
            else:
                logger.info("Notification emails are queued; Resend is not configured yet")
            push_delivery = deliver_pending_push_notifications(storage)
            logger.info("Push notification delivery completed: %s", push_delivery)
        finally:
            storage.close()
    except Exception:
        logger.exception("Daily source scan failed")
    finally:
        source_scan_lock.release()


def _headshot_path(user_id: str) -> Path:
    """The only private on-disk headshot location for one performer."""
    return UPLOADS_DIR / user_id / "headshot.jpg"


def _headshot_check_marker(user_id: str) -> Path:
    """Remember that this uploaded CV has already been assessed for a portrait."""
    return UPLOADS_DIR / user_id / ".headshot_checked"


def _mark_headshot_checked(user_id: str) -> None:
    marker = _headshot_check_marker(user_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()


def _set_cv_headshot_available(user_id: str, artist_dna: dict[str, Any]) -> None:
    physical = artist_dna.setdefault("physical", {})
    physical["headshot_available"] = True
    physical["source"] = "cv"


def _backfill_cv_headshot(user_id: str) -> None:
    """Inspect one older CV once, keeping existing profiles and uploads intact."""
    headshot_path = _headshot_path(user_id)
    if headshot_path.is_file() or _headshot_check_marker(user_id).is_file():
        return
    user_directory = UPLOADS_DIR / user_id
    files = sorted(user_directory.glob("cv.*"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        return
    found = extract_cv_headshot(files[0], headshot_path)
    _mark_headshot_checked(user_id)
    if not found and not headshot_path.is_file():
        return
    storage = StageVivaStorage(DATABASE_PATH)
    try:
        artist = storage.get_artist_for_user(user_id)
        if artist:
            _set_cv_headshot_available(user_id, artist["dna"])
            storage.upsert_artist_for_user(user_id, artist["dna"])
    finally:
        storage.close()


def _analyse_uploaded_cv_in_background(user_id: str, cv_path: str) -> None:
    """Finish a CV analysis after the browser has received its 202 response."""
    storage = StageVivaStorage(DATABASE_PATH)
    try:
        artist_dna = analyse_artist(cv_path)
        headshot_path = _headshot_path(user_id)
        found_new_headshot = extract_cv_headshot(cv_path, headshot_path)
        # A prior confirmed CV headshot remains private and available if a
        # later text-only CV contains no new portrait.
        if found_new_headshot or headshot_path.is_file():
            _set_cv_headshot_available(user_id, artist_dna)
        _mark_headshot_checked(user_id)
        _store_and_match_artist(user_id, artist_dna, storage)
        storage.complete_cv_analysis(user_id)
    except Exception as error:
        logger.exception("CV analysis failed for user %s", user_id)
        storage.complete_cv_analysis(user_id, str(error))
    finally:
        storage.close()


@app.put("/me/artist-dna")
def save_artist_dna(payload: ArtistDNARequest, user: CurrentUser, storage: Storage) -> dict[str, Any]:
    return _store_and_match_artist(user["id"], payload.artist_dna, storage)


@app.get("/me/headshot")
def get_headshot(user: CurrentUser) -> FileResponse:
    """Return the signed-in performer's private CV headshot, if one exists."""
    headshot = _headshot_path(user["id"])
    if not headshot.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No headshot is available yet.")
    return FileResponse(
        headshot, media_type="image/jpeg", filename="stageviva-headshot.jpg",
        headers={"Cache-Control": "private, no-store"},
    )


def _deep_merge(existing: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Merge an editor patch without deleting unrelated Artist DNA evidence."""
    result = json.loads(json.dumps(existing))
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _answer_text(answer: dict[str, Any]) -> str:
    value = answer.get("answer", answer.get("value", ""))
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def _apply_profile_answers_locally(
    artist_dna: dict[str, Any], answers: list[dict[str, Any]],
) -> tuple[dict[str, Any], int]:
    """Save known setup answers immediately, without an unnecessary AI call."""
    updated = json.loads(json.dumps(artist_dna))
    identity = updated.setdefault("identity", {})
    preferences = updated.setdefault("preferences", {})
    applied = 0
    for item in answers:
        question = str(item.get("id", item.get("question", ""))).strip().lower()
        value = _answer_text(item)
        if not value:
            continue
        if question in {"nationality", "what is your nationality?"}:
            identity["nationality"] = value
        elif question in {"gender", "which gender should we use when matching gender-specific roles?"}:
            identity["gender"] = _normalise_gender(value)
        elif question in {"date_of_birth", "date of birth", "what is your date of birth?"}:
            age = _age_from_date_of_birth(value)
            if age is None:
                continue
            identity["date_of_birth"] = value
            identity["age"] = age
        elif question in {"current_location", "where are you currently based?"}:
            identity["location"] = value
        elif question in {"availability", "when are you next available for a new contract?"}:
            preferences["availability"] = value
        elif question in {"relocation", "would you be willing to relocate for the right opportunity?"}:
            preferences["relocation_preferences"] = value
        elif question in {"contract_preferences", "what kinds of opportunity are you looking for?"}:
            selected = [part.strip() for part in value.split(",") if part.strip()]
            preferences["contract_preferences"] = selected
            # Keep the earlier profile field aligned for existing screens.
            preferences["target_roles"] = selected
        else:
            continue
        applied += 1
    return updated, applied


@app.patch("/me/artist-dna")
def update_artist_dna(payload: ArtistDNAUpdateRequest, user: CurrentUser, storage: Storage) -> dict[str, Any]:
    """Save performer-confirmed edits and refresh their recommendation feed."""
    artist = storage.get_artist_for_user(user["id"])
    if not artist:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No Artist DNA yet.")
    if not payload.updates:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="No profile changes supplied.")
    updated_dna = _deep_merge(artist["dna"], payload.updates)
    _synchronise_date_of_birth(updated_dna.setdefault("identity", {}))
    outcome = _store_and_match_artist(user["id"], updated_dna, storage)
    return {"artist_dna": _artist_dna_with_profile_questions(updated_dna), **outcome}


@app.post("/me/artist-dna/answers")
def answer_artist_questions(
    payload: ArtistDNAAnswersRequest, user: CurrentUser, storage: Storage,
) -> dict[str, Any]:
    artist = storage.get_artist_for_user(user["id"])
    if not artist:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No Artist DNA yet.")
    try:
        updated_dna, locally_applied = _apply_profile_answers_locally(artist["dna"], payload.answers)
        if locally_applied:
            # These answers include availability, contract preferences and
            # gender, all of which can change which opportunities are suitable.
            outcome = _store_and_match_artist(user["id"], updated_dna, storage)
        else:
            updated_dna = enrich_artist_dna(artist["dna"], payload.answers)
            outcome = _store_and_match_artist(user["id"], updated_dna, storage)
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Artist DNA update failed: {error}",
        ) from error
    return {"artist_dna": updated_dna, **outcome}


@app.post("/me/cv", status_code=status.HTTP_202_ACCEPTED)
async def upload_cv(
    user: CurrentUser,
    storage: Storage,
    background_tasks: BackgroundTasks,
    cv: UploadFile = File(...),
) -> dict[str, Any]:
    suffix = Path(cv.filename or "").suffix.lower()
    if suffix not in {".pdf", ".docx", ".txt"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Upload a PDF, DOCX, or TXT CV.")
    content = await cv.read()
    if not content or len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CV must be between 1 byte and 10 MB.")
    if not storage.reserve_cv_upload(user["id"], maximum=2):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="You have used both CV uploads. Please edit your performer profile to keep it up to date.",
        )
    user_directory = UPLOADS_DIR / user["id"]
    user_directory.mkdir(parents=True, exist_ok=True)
    cv_path = user_directory / f"cv{suffix}"
    cv_path.write_bytes(content)
    # A replacement CV deserves a fresh portrait check, even if an earlier
    # upload had no usable headshot.
    _headshot_check_marker(user["id"]).unlink(missing_ok=True)
    job = storage.start_cv_analysis(user["id"])
    background_tasks.add_task(_analyse_uploaded_cv_in_background, user["id"], str(cv_path))
    return {
        "status": job["status"], "started_at": job["started_at"],
        "uploads_used": storage.cv_upload_count(user["id"]), "uploads_remaining": 2 - storage.cv_upload_count(user["id"]),
    }


@app.get("/me/cv-analysis")
def get_cv_analysis(user: CurrentUser, storage: Storage) -> dict[str, Any]:
    """Polling endpoint for the setup screen; never keeps a browser request open."""
    job = storage.get_cv_analysis(user["id"])
    if not job:
        return {"status": "not_started", "uploads_used": storage.cv_upload_count(user["id"]), "uploads_remaining": 2 - storage.cv_upload_count(user["id"])}
    response: dict[str, Any] = dict(job)
    response["uploads_used"] = storage.cv_upload_count(user["id"])
    response["uploads_remaining"] = max(0, 2 - response["uploads_used"])
    if job["status"] == "complete":
        artist = storage.get_artist_for_user(user["id"])
        response["artist_dna"] = _artist_dna_with_profile_questions(artist["dna"]) if artist else None
    return response


@app.post("/internal/daily-source-scan", status_code=status.HTTP_202_ACCEPTED)
def trigger_daily_source_scan(
    scheduler_secret: Annotated[str | None, Header(alias="X-StageViva-Scheduler-Secret")] = None,
) -> dict[str, str]:
    """Securely start the daily catalogue refresh from Render Cron."""
    configured_secret = os.getenv("STAGEVIVA_SCHEDULER_SECRET")
    if not configured_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Scheduler is not configured.")
    if not scheduler_secret or not hmac.compare_digest(scheduler_secret, configured_secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid scheduler credential.")
    if not source_scan_lock.acquire(blocking=False):
        return {"status": "already_running"}

    threading.Thread(target=_run_daily_source_scan, daemon=True).start()
    return {"status": "started"}


@app.get("/matches")
def list_matches(
    user: CurrentUser,
    storage: Storage,
    track: str | None = Query(default=None, pattern="^(contract|apprenticeship)$"),
    categories: str | None = None,
) -> list[dict[str, Any]]:
    selected_categories = {
        value.strip().lower() for value in (categories or "").split(",") if value.strip()
    }
    matches = [
        item for item in storage.list_matches_for_user(user["id"])
        if is_stageviva_eligible(item["opportunity"]) and is_current_opportunity(item["opportunity"])
    ]
    return matches_for_filters(
        matches, track=track, categories=selected_categories,
    )


@app.get("/opportunities")
def list_opportunities(user: CurrentUser, storage: Storage) -> list[dict[str, Any]]:
    # Keep this compatibility endpoint presentation-ready as well. The mobile
    # app may use it while the main match feed is loading.
    matches = [
        item for item in storage.list_matches_for_user(user["id"])
        if is_stageviva_eligible(item["opportunity"]) and is_current_opportunity(item["opportunity"])
    ]
    return matches_for_filters(matches, track=None, categories=set())


@app.get("/opportunities/{opportunity_id}")
def get_opportunity(opportunity_id: str, user: CurrentUser, storage: Storage) -> dict[str, Any]:
    """Return a clean detail screen for one recommendation."""
    for item in storage.list_matches_for_user(user["id"]):
        if (item["opportunity_id"] == opportunity_id and is_stageviva_eligible(item["opportunity"])
                and is_current_opportunity(item["opportunity"])):
            return opportunity_detail(item)
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Opportunity not found.")


@app.get("/admin/opportunities")
def admin_list_opportunities(admin: AdminUser, storage: Storage) -> list[dict[str, Any]]:
    """Owner-only import and moderation queue, including hidden listings."""
    return storage.list_all_opportunities()


@app.get("/admin/operations")
def admin_operations(admin: AdminUser, storage: Storage) -> dict[str, Any]:
    """Owner-only operational status for the daily import and rolling backups."""
    recent_runs = storage.list_source_runs(limit=200)
    latest_by_source: dict[str, dict[str, Any]] = {}
    for run in recent_runs:
        latest_by_source.setdefault(str(run["source_name"]), run)
    source_health = []
    for source in get_active_sources():
        latest = latest_by_source.get(source.name)
        result = latest.get("result", {}) if latest else {}
        status_label = "not_run" if latest is None else "healthy"
        if latest and latest["status"] == "failed":
            status_label = "failed"
        elif latest and int(result.get("discovered", 0) or 0) == 0:
            status_label = "warning"
        source_health.append({
            "source": source.name,
            "status": status_label,
            "last_run_at": latest.get("completed_at") if latest else None,
            "discovered": int(result.get("discovered", 0) or 0),
            "new_opportunities": int(result.get("stored_opportunities", 0) or 0),
            "error": latest.get("error") if latest else None,
        })
    return {"sources": source_health, "backups": list_database_backups(DATABASE_PATH)}


@app.get("/admin/performers")
def admin_list_performers(admin: AdminUser, storage: Storage) -> list[dict[str, Any]]:
    """Private creator overview. It exposes profiles, never passwords or CV files."""
    return storage.list_performers_for_admin()


@app.patch("/admin/performers/{user_id}/membership")
def admin_update_performer_membership(
    user_id: str, payload: AdminMembershipUpdateRequest, admin: AdminUser, storage: Storage,
) -> dict[str, Any]:
    updated = storage.update_membership_tier(user_id, payload.membership_tier)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Performer not found.")
    return {
        "id": updated["id"], "email": updated["email"], "display_name": updated["display_name"],
        "membership_tier": _membership_tier(updated), "access": _access_for_user(updated),
    }


@app.patch("/admin/opportunities/{opportunity_id}")
def admin_update_opportunity(
    opportunity_id: str, payload: AdminOpportunityUpdateRequest, admin: AdminUser, storage: Storage,
) -> dict[str, Any]:
    changed = False
    fields = {
        key: value.strip()
        for key, value in payload.model_dump(exclude={"visible"}, exclude_none=True).items()
    }
    if payload.official_url is not None and not payload.official_url.startswith(("https://", "http://")):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Use the official http(s) listing URL.")
    updated_opportunity = storage.update_opportunity_details(opportunity_id, fields) if fields else None
    if updated_opportunity is not None:
        changed = True
    if payload.visible is not None:
        changed = storage.set_opportunity_visibility(opportunity_id, payload.visible) or changed
    if not changed and not any(item["id"] == opportunity_id for item in storage.list_all_opportunities()):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Opportunity not found.")
    match_result = (
        _match_opportunity_against_registered_artists(opportunity_id, updated_opportunity, storage)
        if updated_opportunity is not None else {"matched_artists": 0, "queued_notifications": 0}
    )
    return {"ok": True, **match_result}


@app.post("/admin/opportunities", status_code=status.HTTP_201_CREATED)
def admin_add_opportunity(
    payload: AdminManualOpportunityRequest, admin: AdminUser, storage: Storage,
) -> dict[str, Any]:
    if not payload.official_url.startswith(("https://", "http://")):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Use the official http(s) listing URL.")
    opportunity = {
        "identity": {
            "title": {"value": payload.title.strip()},
            "organisation": {"value": payload.organisation.strip()},
            "opportunity_type": {"value": payload.role_summary.strip()},
            "description": {"value": payload.description.strip()},
        },
        "location": {"city": {"value": payload.location.strip()}, "country": {"value": "unknown"}},
        "dates": {"application_deadline": {"value": payload.deadline.strip()}, "audition_dates": {"value": []}},
        "contract_and_compensation": {"contract_type": {"value": payload.contract_type.strip()}},
        "application": {"application_url": {"value": payload.official_url.strip()}},
        "source": {"official_url": {"value": payload.official_url.strip()}},
    }
    if not is_stageviva_eligible(opportunity) or not is_current_opportunity(opportunity):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="This listing is not a current paid or qualifying transition opportunity.")
    item = DiscoveredOpportunity(
        payload.title.strip(), payload.official_url.strip(), "StageViva manual review",
        payload.official_url.strip(), "manual", description=payload.description.strip(),
    )
    opportunity_id = storage.upsert_opportunity(item, opportunity)
    return {"id": opportunity_id, **_match_opportunity_against_registered_artists(opportunity_id, opportunity, storage)}


@app.get("/notifications")
def list_notifications(user: CurrentUser, storage: Storage) -> list[dict[str, Any]]:
    return storage.list_notifications_for_user(user["id"])


@app.post("/notifications/{notification_id}/read")
def mark_notification_read(notification_id: str, user: CurrentUser, storage: Storage) -> dict[str, bool]:
    if not storage.mark_notification_read(user["id"], notification_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found.")
    return {"ok": True}


@app.put("/me/notification-preferences")
def update_notification_preferences(
    payload: NotificationPreferencesRequest, user: CurrentUser, storage: Storage,
) -> dict[str, Any]:
    updated = storage.update_notification_preferences(
        user["id"], email=payload.email_notifications, in_app=payload.in_app_notifications,
    )
    return _public_user(updated)["notification_preferences"]


@app.get("/me/push/public-key")
def get_push_public_key(user: CurrentUser, storage: Storage) -> dict[str, str]:
    """Give the installed app the public VAPID key needed to subscribe."""
    return {"public_key": public_vapid_key(storage)}


@app.post("/me/push-subscriptions", status_code=status.HTTP_204_NO_CONTENT)
def save_push_subscription(payload: PushSubscriptionRequest, user: CurrentUser, storage: Storage) -> None:
    if not payload.keys.get("p256dh") or not payload.keys.get("auth"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="A browser push subscription needs p256dh and auth keys.")
    storage.upsert_push_subscription(user["id"], payload.model_dump())


@app.delete("/me/push-subscriptions", status_code=status.HTTP_204_NO_CONTENT)
def remove_push_subscription(payload: PushSubscriptionRequest, user: CurrentUser, storage: Storage) -> None:
    storage.remove_push_subscription(user["id"], payload.endpoint)


@app.post("/me/push-subscriptions/test")
def test_push_subscription(user: CurrentUser, storage: Storage) -> dict[str, int]:
    return send_test_push_notification(storage, user["id"])
