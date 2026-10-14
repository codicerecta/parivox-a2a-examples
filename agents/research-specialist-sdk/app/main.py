"""Application assembly and entry point.

a2a-sdk 1.1.2 has no all-in-one application class: routes are built and mounted onto
a Starlette app explicitly, which is convenient here because it leaves room for the
auth middleware and a health check.
"""

from __future__ import annotations

import logging

from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.utils.constants import DEFAULT_RPC_URL
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from .auth import ApiKeyMiddleware
from .card import build_agent_card
from .executor import ResearchExecutor
from .handler import ConformingRequestHandler
from .research import build_provider
from .settings import Settings, load_settings

logger = logging.getLogger("a2a.main")


async def _healthz(_request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def build_app(settings: Settings) -> Starlette:
    """Assemble the ASGI application for the given settings."""
    card = build_agent_card(
        settings.public_url,
        auth_enabled=settings.auth_enabled,
        typical_duration_seconds=settings.typical_duration_seconds,
    )
    provider = build_provider(
        openai_api_key=settings.openai_api_key,
        model=settings.model,
        max_sources=settings.max_sources,
        canned_delay_seconds=settings.canned_delay_seconds,
    )
    handler = ConformingRequestHandler(
        agent_executor=ResearchExecutor(provider),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )

    routes = [
        *create_agent_card_routes(card),
        *create_jsonrpc_routes(handler, DEFAULT_RPC_URL),
        Route("/healthz", _healthz, methods=["GET"]),
    ]
    app = Starlette(routes=routes)

    if settings.auth_enabled:
        assert settings.api_key is not None  # guaranteed by load_settings
        app.add_middleware(ApiKeyMiddleware, api_key=settings.api_key)
    else:
        logger.warning(
            "Serving WITHOUT authentication (A2A_ALLOW_ANONYMOUS is set). "
            "This is intended for local development and CI only."
        )

    return app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    settings = load_settings()
    app = build_app(settings)

    import uvicorn

    logger.info(
        "Research specialist listening on port %s (auth=%s, live_search=%s)",
        settings.port,
        settings.auth_enabled,
        settings.can_search,
    )
    uvicorn.run(app, host="0.0.0.0", port=settings.port, log_level="info")  # noqa: S104


if __name__ == "__main__":
    main()
