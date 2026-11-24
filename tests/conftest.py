"""Shared pytest fixtures for isolated SQLite databases, in-process A2A servers, and ASGI Gateway."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
import sys

from fastapi import FastAPI
import httpx
import pytest
import pytest_asyncio

from procureflow_ai.api.a2a_servers import create_inprocess_a2a_httpx_client
from procureflow_ai.api.gateway import create_gateway_app
from procureflow_ai.db.seed import init_and_seed_db
from procureflow_ai.observability.context import (
    current_service_name,
    current_session_id,
    current_sse_queue,
)
from procureflow_ai.observability.tracing import setup_telemetry


def pytest_configure() -> None:
    """Ensure repository root is on sys.path so `evals` imports cleanly under `.venv/bin/pytest`."""
    repo_root = str(Path(__file__).resolve().parents[1])
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)


@pytest.fixture(autouse=True)
def isolated_dbs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    """Provision fresh, isolated domain and session SQLite databases per test."""
    domain_db = tmp_path / "procureflow_domain_test.db"
    session_db = tmp_path / "procureflow_sessions_test.db"

    monkeypatch.setenv("PROCUREFLOW_DOMAIN_DB_PATH", str(domain_db))
    monkeypatch.setenv("PROCUREFLOW_SESSIONS_DB_PATH", str(session_db))
    monkeypatch.setenv("ENABLE_CLOUD_TRACE", "false")

    current_session_id.set("")
    current_service_name.set("gateway")
    current_sse_queue.set(None)

    # Ensure the singleton SQLiteSpanExporter resolves dynamically via env var per test
    setup_telemetry(service_name="gateway", db_path=None)
    init_and_seed_db(domain_db)

    return domain_db, session_db


@pytest.fixture
def domain_db_path(isolated_dbs: tuple[Path, Path]) -> Path:
    """Return path to the isolated seeded domain SQLite database."""
    return isolated_dbs[0]


@pytest.fixture
def session_db_path(isolated_dbs: tuple[Path, Path]) -> Path:
    """Return path to the isolated ADK session SQLite database."""
    return isolated_dbs[1]


@pytest_asyncio.fixture
async def a2a_httpx_client(
    isolated_dbs: tuple[Path, Path],
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an in-process ASGI-routed HTTPX client serving ports 8001, 8002, and 8003."""
    del isolated_dbs
    client = create_inprocess_a2a_httpx_client()
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def gateway_app(
    isolated_dbs: tuple[Path, Path],
    a2a_httpx_client: httpx.AsyncClient,
) -> FastAPI:
    """Build a FastAPI Gateway wired to in-process A2A servers without opening network sockets."""
    del isolated_dbs
    return create_gateway_app(
        httpx_client=a2a_httpx_client,
        delegate_settlement_over_a2a=False,
    )


@pytest_asyncio.fixture
async def gateway_client(
    gateway_app: FastAPI,
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an ASGI-backed HTTPX client targeting the FastAPI Gateway."""
    transport = httpx.ASGITransport(app=gateway_app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        yield client
