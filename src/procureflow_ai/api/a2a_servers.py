"""Starlette A2A sub-servers (Ports 8001, 8002, 8003) and in-process ASGI transport router."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from urllib.parse import urlparse

from google.adk.a2a.utils.agent_to_a2a import to_a2a
from google.adk.agents.base_agent import BaseAgent
import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..agents.catalog_agent import create_catalog_agent
from ..agents.policy_agent import create_policy_agent
from ..agents.settlement_agent import create_settlement_agent
from ..config import get_settings
from ..db.seed import init_and_seed_db
from ..observability.tracing import (
    A2ATraceContextMiddleware,
    create_traced_httpx_client,
    setup_telemetry,
)


class _LazyLifespanASGIWrapper:
    """Ensures Starlette `to_a2a` lifespan routes initialize and close cleanly under `httpx.ASGITransport`."""

    def __init__(self, starlette_app: Starlette, middleware_app: Any) -> None:
        self._starlette_app = starlette_app
        self._middleware_app = middleware_app
        self._lock = asyncio.Lock()
        self._lazy_ctx: Any = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._starlette_app, name)

    async def _ensure_ready(self) -> None:
        if getattr(self._starlette_app.state, "_procureflow_ready", False):
            return
        async with self._lock:
            if getattr(self._starlette_app.state, "_procureflow_ready", False):
                return
            cm = self._starlette_app.router.lifespan_context(self._starlette_app)
            await cm.__aenter__()
            self._lazy_ctx = cm

    async def aclose(self) -> None:
        """Cleanly exit the lazy lifespan context inside the running event loop before GC."""
        async with self._lock:
            if self._lazy_ctx is not None:
                ctx = self._lazy_ctx
                self._lazy_ctx = None
                await ctx.__aexit__(None, None, None)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") == "lifespan":
            await self._starlette_app(scope, receive, send)
            return
        await self._ensure_ready()
        await self._middleware_app(scope, receive, send)


def _build_wrapped_a2a_server(
    *,
    agent: BaseAgent,
    service_name: str,
    host: str,
    port: int,
    protocol: str = "http",
) -> Any:
    """Wrap an ADK `LlmAgent` with `to_a2a()`, health/card aliases, and W3C trace middleware."""

    async def _health_endpoint(_request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "service": service_name,
                "agent": agent.name,
                "port": port,
            }
        )

    @asynccontextmanager
    async def _lifespan(app: Starlette) -> AsyncIterator[None]:
        init_and_seed_db()
        setup_telemetry(service_name=service_name)

        existing_paths = {getattr(r, "path", None) for r in app.routes}
        if "/health" not in existing_paths:
            app.routes.append(Route("/health", endpoint=_health_endpoint, methods=["GET"]))

        # Expose `/.well-known/agent.json` alias alongside A2A 1.0 `/.well-known/agent-card.json`
        if "/.well-known/agent.json" not in existing_paths:
            for r in list(app.routes):
                if getattr(r, "path", None) == "/.well-known/agent-card.json":
                    app.routes.append(
                        Route(
                            "/.well-known/agent.json",
                            endpoint=r.endpoint,
                            methods=["GET"],
                        )
                    )
                    break

        app.state._procureflow_ready = True
        try:
            yield
        finally:
            app.state._procureflow_ready = False

    starlette_app = to_a2a(
        agent,
        host=host,
        port=port,
        protocol=protocol,
        lifespan=_lifespan,
    )
    traced_app = A2ATraceContextMiddleware(
        starlette_app,
        service_name=service_name,
        agent_name=agent.name,
    )
    return _LazyLifespanASGIWrapper(starlette_app, traced_app)


def create_catalog_a2a_app(
    *,
    host: str = "localhost",
    port: int | None = None,
    protocol: str = "http",
    model: str | None = None,
) -> Any:
    """Build the Catalog & Sourcing A2A Starlette application (Port 8001)."""
    eff_port = port or get_settings().catalog_port
    agent = create_catalog_agent(model=model)
    return _build_wrapped_a2a_server(
        agent=agent,
        service_name="catalog",
        host=host,
        port=eff_port,
        protocol=protocol,
    )


def create_policy_a2a_app(
    *,
    host: str = "localhost",
    port: int | None = None,
    protocol: str = "http",
    model: str | None = None,
) -> Any:
    """Build the Policy & FinOps Risk A2A Starlette application (Port 8002)."""
    eff_port = port or get_settings().policy_port
    agent = create_policy_agent(model=model)
    return _build_wrapped_a2a_server(
        agent=agent,
        service_name="policy",
        host=host,
        port=eff_port,
        protocol=protocol,
    )


def create_settlement_a2a_app(
    *,
    host: str = "localhost",
    port: int | None = None,
    protocol: str = "http",
    model: str | None = None,
) -> Any:
    """Build the AP2 Settlement A2A Starlette application (Port 8003)."""
    eff_port = port or get_settings().settlement_port
    agent = create_settlement_agent(model=model)
    return _build_wrapped_a2a_server(
        agent=agent,
        service_name="settlement",
        host=host,
        port=eff_port,
        protocol=protocol,
    )


class InProcessA2ARouterTransport(httpx.AsyncBaseTransport):
    """Routes HTTP requests by `(host, port)` to in-memory `ASGITransport` A2A sub-servers.

    Enables zero-socket, deterministic end-to-end A2A JSON-RPC + W3C `traceparent` testing!
    """

    def __init__(self, port_to_app: dict[int, Any]) -> None:
        self._apps = dict(port_to_app)
        self._transports = {
            port: httpx.ASGITransport(app=app) for port, app in port_to_app.items()
        }

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        port = request.url.port
        if port is None:
            parsed = urlparse(str(request.url))
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        transport = self._transports.get(int(port))
        if transport is None:
            raise httpx.ConnectError(
                f"No in-process A2A app registered on port {port} for {request.url}"
            )
        return await transport.handle_async_request(request)

    async def aclose(self) -> None:
        for t in self._transports.values():
            await t.aclose()
        for app in self._apps.values():
            aclose_fn = getattr(app, "aclose", None)
            if callable(aclose_fn):
                await aclose_fn()


def create_inprocess_a2a_httpx_client(
    *,
    catalog_app: Any | None = None,
    policy_app: Any | None = None,
    settlement_app: Any | None = None,
) -> httpx.AsyncClient:
    """Create a traced `httpx.AsyncClient` routing `localhost:8001..8003` to in-memory A2A apps."""
    settings = get_settings()
    c_app = catalog_app or create_catalog_a2a_app(
        host="localhost", port=settings.catalog_port
    )
    p_app = policy_app or create_policy_a2a_app(
        host="localhost", port=settings.policy_port
    )
    s_app = settlement_app or create_settlement_a2a_app(
        host="localhost", port=settings.settlement_port
    )
    router = InProcessA2ARouterTransport(
        {
            settings.catalog_port: c_app,
            settings.policy_port: p_app,
            settings.settlement_port: s_app,
        }
    )
    return create_traced_httpx_client(transport=router, timeout=120.0)
