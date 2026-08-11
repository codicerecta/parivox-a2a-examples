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
# What a live-search run of this agent typically costs in wall-clock seconds,
# measured against the OpenAI provider rather than guessed. It is declared on
# the Agent Card so a caller can decide what to say — and whether to wait —
# before it sends anything.
LIVE_TYPICAL_DURATION_SECONDS = 75.0
# The floor for the canned provider, which answers as fast as it is asked. A
# caller that races a few seconds for an inline answer wins that race against
# this configuration, which is the point of declaring it honestly.
CANNED_TYPICAL_DURATION_SECONDS = 2.0


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
    canned_delay_seconds: float
    # Declared on the Agent Card. Derived from how this instance is actually
    # configured rather than fixed, because a live-search deployment and a
    # canned one differ by an order of magnitude and a single number would be
    # wrong for both — which is the failure mode a declared duration exists to
    # avoid.
    typical_duration_seconds: float

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

    # Only affects the keyless canned provider. It exists so a client can be
    # exercised against realistic minute-scale timing — progress arriving over
    # time, cancelling a task mid-flight — without a model key or a real bill.
    try:
        canned_delay_seconds = float(source.get("CANNED_DELAY_SECONDS", "0"))
    except ValueError as exc:
        raise ConfigurationError("CANNED_DELAY_SECONDS is not a number") from exc
    if canned_delay_seconds < 0:
        raise ConfigurationError("CANNED_DELAY_SECONDS must not be negative")

    openai_api_key = (source.get("OPENAI_API_KEY") or "").strip() or None
    try:
        typical_duration_seconds = float(
            source.get("TYPICAL_DURATION_SECONDS")
            or _derive_typical_duration(
                can_search=bool(openai_api_key), canned_delay_seconds=canned_delay_seconds
            )
        )
    except ValueError as exc:
        raise ConfigurationError("TYPICAL_DURATION_SECONDS is not a number") from exc
    if typical_duration_seconds <= 0:
        raise ConfigurationError("TYPICAL_DURATION_SECONDS must be positive")

    return Settings(
        api_key=api_key,
        allow_anonymous=allow_anonymous,
        port=port,
        openai_api_key=openai_api_key,
        model=(source.get("RESEARCH_MODEL") or "").strip() or DEFAULT_MODEL,
        max_sources=max_sources,
        # Advertised in the Agent Card so clients know where to send RPC calls.
        # Inside a VPC this is the Cloud Map private DNS name.
        public_url=(source.get("PUBLIC_URL") or "").strip()
        or f"http://localhost:{source.get('PORT', DEFAULT_PORT)}/",
        canned_delay_seconds=canned_delay_seconds,
        typical_duration_seconds=typical_duration_seconds,
    )


def _derive_typical_duration(*, can_search: bool, canned_delay_seconds: float) -> float:
    """How long this configuration actually takes, as far as it can be known.

    Declaring a duration is only worth doing if the number is true, so it is
    computed from the provider that will really run rather than written down
    once. The canned provider sleeps its delay before each of two progress
    steps, so its cost is knowable exactly; live search is not, and 75 seconds
    is what the OpenAI provider has been observed to take.
    """
    if can_search:
        return LIVE_TYPICAL_DURATION_SECONDS
    return max(CANNED_TYPICAL_DURATION_SECONDS, 2 * canned_delay_seconds + 1.0)


__all__ = [
    "CANNED_TYPICAL_DURATION_SECONDS",
    "LIVE_TYPICAL_DURATION_SECONDS",
    "ConfigurationError",
    "Settings",
    "load_settings",
]
