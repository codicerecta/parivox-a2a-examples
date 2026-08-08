"""Shared-secret authentication for RPC calls.

The Agent Card is served unauthenticated so that clients — and the conformance kit —
can discover the agent. Everything else requires the shared secret.
"""

from __future__ import annotations

import hmac
import logging

from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("a2a.auth")

API_KEY_HEADER = "X-API-Key"

# Discovery is deliberately open: a card is public information, and the TCK fetches
# it before it can authenticate to anything.
_UNAUTHENTICATED_PATHS = frozenset({AGENT_CARD_WELL_KNOWN_PATH, "/healthz"})


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Rejects requests that do not carry the expected API key."""

    def __init__(self, app, api_key: str) -> None:
        super().__init__(app)
        if not api_key:
            raise ValueError("ApiKeyMiddleware requires a non-empty API key")
        self._api_key = api_key

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _UNAUTHENTICATED_PATHS:
            return await call_next(request)

        presented = request.headers.get(API_KEY_HEADER, "")
        # Constant-time comparison: this is a shared secret, and a timing oracle on
        # it is free to avoid.
        if not presented or not hmac.compare_digest(presented, self._api_key):
            logger.warning("Rejected unauthenticated request to %s", request.url.path)
            return JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": API_KEY_HEADER},
            )
        return await call_next(request)


__all__ = ["API_KEY_HEADER", "ApiKeyMiddleware"]
