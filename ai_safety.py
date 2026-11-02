"""Server-side safeguards for every billable AI request."""

from __future__ import annotations

import os
import json
import threading
import time
import tempfile
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path


class AIRequestsDisabledError(RuntimeError):
    """Raised before a request can be sent to the AI provider."""


_usage_lock = threading.Lock()


def _allowed_models() -> frozenset[str]:
    configured = os.getenv("STAGEVIVA_ALLOWED_AI_MODELS", "gpt-4o-mini")
    return frozenset(model.strip() for model in configured.split(",") if model.strip())


def require_ai_requests_enabled(model: str | None = None) -> None:
    """Fail closed unless AI is enabled and the requested model is approved."""
    enabled = os.getenv("STAGEVIVA_AI_REQUESTS_ENABLED", "false").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        raise AIRequestsDisabledError(
            "AI analysis is temporarily paused while StageViva reviews usage."
        )
    if model and model not in _allowed_models():
        raise AIRequestsDisabledError(
            f"AI model {model!r} is not allowed for this StageViva project."
        )


def _usage_path() -> Path:
    configured = os.getenv("STAGEVIVA_AI_USAGE_STATE_PATH")
    if configured:
        return Path(configured)
    persistent = Path("/var/data")
    if persistent.is_dir():
        return persistent / "ai-request-usage.json"
    return Path("data/ai-request-usage.json")


@contextmanager
def _locked_usage(path: Path):
    """Serialize processes sharing one persistent ledger (Windows and Linux).

    The sidecar must remain at a stable path: replacing the JSON file must not
    replace the inode on which another process holds its lock. Separate hosts
    with separate disks do NOT share this allowance.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + ".lock").open("a+b") as lock:
        deadline = time.monotonic() + 5
        acquired = False
        while not acquired:
            try:
                if os.name == "nt":
                    import msvcrt
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                if time.monotonic() >= deadline:
                    raise AIRequestsDisabledError("AI usage accounting is busy; please retry later.") from None
                time.sleep(0.02)
        try:
            yield lock
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _read_usage(path: Path, lock, today: str) -> dict:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        lock.seek(0)
        if lock.read(1):
            raise ValueError("Previously initialized ledger is missing")
        return {"date": today, "count": 0, "operations": {}}
    if not isinstance(state, dict):
        raise ValueError("Invalid usage ledger")
    recorded_date = date.fromisoformat(state["date"])
    count, operations = state["count"], state["operations"]
    if (type(count) is not int or count < 0 or not isinstance(operations, dict)
            or any(not isinstance(key, str) or type(value) is not int or value < 0
                   for key, value in operations.items())
            or sum(operations.values()) != count
            or recorded_date > date.fromisoformat(today)):
        raise ValueError("Invalid usage ledger")
    if recorded_date.isoformat() != today:
        return {"date": today, "count": 0, "operations": {}}
    return state


def _write_usage(path: Path, state: dict) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            json.dump(state, output, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def reserve_ai_request(model: str, operation: str) -> None:
    """Reserve a logical AI operation before contacting the provider.

    Failed provider calls still count. This is a request allowance on a shared
    persistent filesystem, not a currency cutoff or a count of SDK retries.
    Invalid accounting blocks requests instead of resetting the allowance.
    """
    require_ai_requests_enabled(model)
    try:
        daily_limit = int(os.getenv("STAGEVIVA_AI_DAILY_REQUEST_LIMIT", "100"))
        if daily_limit < 1:
            raise ValueError("Daily request limit must be positive")
        path = _usage_path()
        with _usage_lock, _locked_usage(path) as lock:
            today = datetime.now(timezone.utc).date().isoformat()
            state = _read_usage(path, lock, today)
            if state["count"] >= daily_limit:
                raise AIRequestsDisabledError(
                    f"StageViva's daily AI safety limit of {daily_limit} requests has been reached."
                )
            state["count"] += 1
            state["operations"][operation] = state["operations"].get(operation, 0) + 1
            _write_usage(path, state)
            # Remember successful initialization so losing the ledger cannot
            # silently recreate today's allowance. Preserve both files.
            lock.seek(0)
            if not lock.read(1):
                lock.write(b"1")
                lock.flush()
                os.fsync(lock.fileno())
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        raise AIRequestsDisabledError(
            "AI usage accounting is unavailable. Analysis is paused until it is checked."
        ) from None
