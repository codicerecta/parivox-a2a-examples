"""Environment-driven configuration for the research specialist.

Everything this agent needs comes from environment variables. There is no shared
config source and no private package dependency — that is what keeps the agent
independently operable, and honest about being an external service.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_MODEL = "gpt-4.1"
DEFAULT_PORT = 8080


class ConfigurationError(RuntimeError):
    """Raised at startup when the environment is not safe to serve from."""


@dataclass(frozen=True)
class Settings:
    """Resolved runtime configuration."""

    api_key: str | None
    allow_anonymous: bool
    port: int
    openai_api_key: str | None
    model: str
    max_sources: int
    public_url: str

    @property
    def auth_enabled(self) -> bool:
        return not self.allow_anonymous

    @property
    def can_search(self) -> bool:
        """Whether live web research is possible, as opposed to the canned fallback."""
        return bool(self.openai_api_key)


_TRUTHY = {"1", "true", "yes", "on"}


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """Read settings from the environment, refusing unsafe combinations.

    Auth fails closed: an agent that serves without an API key has to be asked for
    explicitly via A2A_ALLOW_ANONYMOUS. Forgetting to set A2A_API_KEY is a startup
    error, not a silently open endpoint.
    """
    source = dict(os.environ if env is None else env)

    api_key = (source.get("A2A_API_KEY") or "").strip() or None
    allow_anonymous = source.get("A2A_ALLOW_ANONYMOUS", "").strip().lower() in _TRUTHY

    if not api_key and not allow_anonymous:
        raise ConfigurationError(
            "Refusing to start: set A2A_API_KEY, or set A2A_ALLOW_ANONYMOUS=1 to "
            "serve without authentication (local development and CI only)."
        )

    try:
        port = int(source.get("PORT", DEFAULT_PORT))
    except ValueError as exc:
        raise ConfigurationError(f"PORT is not an integer: {source.get('PORT')!r}") from exc

    try:
        max_sources = int(source.get("RESEARCH_MAX_SOURCES", "8"))
    except ValueError as exc:
        raise ConfigurationError("RESEARCH_MAX_SOURCES is not an integer") from exc

    return Settings(
        api_key=api_key,
        allow_anonymous=allow_anonymous,
        port=port,
        openai_api_key=(source.get("OPENAI_API_KEY") or "").strip() or None,
        model=(source.get("RESEARCH_MODEL") or "").strip() or DEFAULT_MODEL,
        max_sources=max_sources,
        # Advertised in the Agent Card so clients know where to send RPC calls.
        # Inside a VPC this is the Cloud Map private DNS name.
        public_url=(source.get("PUBLIC_URL") or "").strip()
        or f"http://localhost:{source.get('PORT', DEFAULT_PORT)}/",
    )


__all__ = ["ConfigurationError", "Settings", "load_settings"]
