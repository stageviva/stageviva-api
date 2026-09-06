"""Lovable-ready REST API for StageViva."""

from __future__ import annotations

import os
import secrets
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
from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Query, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field
from pwdlib import PasswordHash

from artist_intelligence import analyse_artist, enrich_artist_dna
from match_service import match_artist_to_opportunity
from opportunity_policy import is_stageviva_eligible
from opportunity_presentation import matches_for_filters, opportunity_detail
from source_registry import get_active_sources
from storage import StageVivaStorage

load_dotenv()

DATABASE_PATH = os.getenv("STAGEVIVA_DB", "stageviva.db")
UPLOADS_DIR = Path(os.getenv("STAGEVIVA_UPLOADS_DIR", "data/uploads"))
TOKEN_LIFETIME_SECONDS = 60 * 60 * 24 * 14
password_hasher = PasswordHash.recommended()
bearer_scheme = HTTPBearer(auto_error=False)
logger = logging.getLogger("stageviva.auth")
SUPPORTED_EXTERNAL_JWT_ALGORITHMS = frozenset({
    "RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "EdDSA",
})
MEMBERSHIP_TIERS = frozenset({"beta", "free", "pro", "school"})


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


@asynccontextmanager
async def lifespan(_: FastAPI):
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    # If the service is restarted mid-analysis, resume from the securely saved
    # CV instead of leaving the performer on a permanent loading screen.
    storage = StageVivaStorage(DATABASE_PATH)
    try:
        pending_users = storage.list_processing_cv_analyses()
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
    return _public_user(storage.update_user_profile(user["id"], payload.display_name, payload.profile))


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


def _matching_profile_questions(artist_dna: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only short, actionable questions that influence matching."""
    identity = artist_dna.get("identity", {})
    eligibility = artist_dna.get("eligibility", {})
    preferences = artist_dna.get("preferences", {})
    questions: list[dict[str, Any]] = []
    if _is_missing(identity.get("date_of_birth")):
        questions.append({"id": "date_of_birth", "question": "What is your date of birth?", "type": "date"})
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


def _store_and_match_artist(user_id: str, artist_dna: dict[str, Any], storage: StageVivaStorage) -> dict[str, int]:
    artist_id = storage.upsert_artist_for_user(user_id, artist_dna)
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


def _analyse_uploaded_cv_in_background(user_id: str, cv_path: str) -> None:
    """Finish a CV analysis after the browser has received its 202 response."""
    storage = StageVivaStorage(DATABASE_PATH)
    try:
        artist_dna = analyse_artist(cv_path)
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
            storage.upsert_artist_for_user(user["id"], updated_dna)
            outcome = {"matched_opportunities": 0, "queued_notifications": 0}
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
    user_directory = UPLOADS_DIR / user["id"]
    user_directory.mkdir(parents=True, exist_ok=True)
    cv_path = user_directory / f"cv{suffix}"
    cv_path.write_bytes(content)
    job = storage.start_cv_analysis(user["id"])
    background_tasks.add_task(_analyse_uploaded_cv_in_background, user["id"], str(cv_path))
    return {"status": job["status"], "started_at": job["started_at"]}


@app.get("/me/cv-analysis")
def get_cv_analysis(user: CurrentUser, storage: Storage) -> dict[str, Any]:
    """Polling endpoint for the setup screen; never keeps a browser request open."""
    job = storage.get_cv_analysis(user["id"])
    if not job:
        return {"status": "not_started"}
    response: dict[str, Any] = dict(job)
    if job["status"] == "complete":
        artist = storage.get_artist_for_user(user["id"])
        response["artist_dna"] = _artist_dna_with_profile_questions(artist["dna"]) if artist else None
    return response


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
        if is_stageviva_eligible(item["opportunity"])
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
        if is_stageviva_eligible(item["opportunity"])
    ]
    return matches_for_filters(matches, track=None, categories=set())


@app.get("/opportunities/{opportunity_id}")
def get_opportunity(opportunity_id: str, user: CurrentUser, storage: Storage) -> dict[str, Any]:
    """Return a clean detail screen for one recommendation."""
    for item in storage.list_matches_for_user(user["id"]):
        if item["opportunity_id"] == opportunity_id and is_stageviva_eligible(item["opportunity"]):
            return opportunity_detail(item)
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Opportunity not found.")


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
