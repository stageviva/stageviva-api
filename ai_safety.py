"""Emergency, server-side guard for billable AI requests."""

from __future__ import annotations

import os


class AIRequestsDisabledError(RuntimeError):
    """Raised before a request can be sent to the AI provider."""


def require_ai_requests_enabled() -> None:
    """Fail closed unless an operator has explicitly enabled billable AI work."""
    enabled = os.getenv("STAGEVIVA_AI_REQUESTS_ENABLED", "false").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        raise AIRequestsDisabledError(
            "AI analysis is temporarily paused while StageViva reviews usage."
        )
