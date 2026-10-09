"""Server-side safeguards for every billable AI request."""

from __future__ import annotations

import os
import json
import threading
from datetime import datetime, timezone
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


def reserve_ai_request(model: str, operation: str) -> None:
    """Reserve one request against a persistent UTC daily hard limit.

    The reservation happens before contacting OpenAI, so concurrent workers or
    retries cannot exceed the configured server-side allowance. Failed provider
    calls intentionally still count: this is a safety ceiling, not billing
    reconciliation.
    """
    require_ai_requests_enabled(model)
    daily_limit = max(1, int(os.getenv("STAGEVIVA_AI_DAILY_REQUEST_LIMIT", "100")))
    today = datetime.now(timezone.utc).date().isoformat()
    path = _usage_path()

    with _usage_lock:
        state: dict[str, object] = {"date": today, "count": 0, "operations": {}}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and loaded.get("date") == today:
                state = loaded
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass

        count = int(state.get("count", 0))
        if count >= daily_limit:
            raise AIRequestsDisabledError(
                f"StageViva's daily AI safety limit of {daily_limit} requests has been reached."
            )

        operations = state.get("operations")
        if not isinstance(operations, dict):
            operations = {}
        operations[operation] = int(operations.get(operation, 0)) + 1
        state = {"date": today, "count": count + 1, "operations": operations}
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        temporary.replace(path)
