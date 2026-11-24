"""OpenTelemetry TracerProvider, SQLiteSpanExporter, and W3C A2A HTTP/ASGI propagation."""

from __future__ import annotations

from collections.abc import MutableMapping, Sequence
import json
import logging
from pathlib import Path
from typing import Any

import httpx
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from ..config import get_domain_db_path, get_settings
from ..db.queries import insert_spans_sync
from .context import current_service_name, current_session_id

logger = logging.getLogger(__name__)

SESSION_ID_HEADER = "x-procureflow-session-id"
A2A_VERSION_HEADER = "A2A-Version"
A2A_VERSION_VALUE = "1.0"

_propagator = TraceContextTextMapPropagator()
_provider_initialized = False
_sqlite_exporter_instance: SQLiteSpanExporter | None = None


class SQLiteSpanExporter(SpanExporter):
    """Exports OpenTelemetry spans into the local `telemetry_spans` SQLite table."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._explicit_db_path = Path(db_path).resolve() if db_path else None

    def set_db_path(self, db_path: Path | str | None) -> None:
        """Update explicit SQLite DB target (or None to resolve dynamically from env)."""
        self._explicit_db_path = Path(db_path).resolve() if db_path else None

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        rows: list[dict[str, Any]] = []
        target_db = self._explicit_db_path or get_domain_db_path()
        for sp in spans:
            ctx = sp.get_span_context()
            if not ctx or not ctx.is_valid:
                continue
            attrs = dict(sp.attributes or {})
            res_attrs = dict(sp.resource.attributes) if sp.resource else {}
            session_id = str(
                attrs.get("procureflow.session_id")
                or attrs.get("gcp.vertex.agent.session_id")
                or current_session_id.get()
                or ""
            )
            service_name = str(
                attrs.get("procureflow.service_name")
                or res_attrs.get("service.name")
                or current_service_name.get()
                or "gateway"
            )
            agent_name = str(
                attrs.get("procureflow.agent_name")
                or attrs.get("gen_ai.agent.name")
                or ""
            )
            start_ns = sp.start_time or 0
            end_ns = sp.end_time or start_ns
            start_ms = round(start_ns / 1_000_000.0, 3)
            duration_ms = round(max(0.0, (end_ns - start_ns) / 1_000_000.0), 3)
            parent_span_id = (
                f"{sp.parent.span_id:016x}"
                if sp.parent and sp.parent.is_valid
                else None
            )
            status_str = (
                "ERROR"
                if sp.status and sp.status.status_code == StatusCode.ERROR
                else "OK"
            )
            clean_attrs = {
                str(k): (
                    v
                    if isinstance(v, (str, int, float, bool)) or v is None
                    else [str(x) for x in v]
                    if isinstance(v, (list, tuple))
                    else str(v)
                )
                for k, v in attrs.items()
            }
            rows.append(
                {
                    "span_id": f"{ctx.span_id:016x}",
                    "trace_id": f"{ctx.trace_id:032x}",
                    "parent_span_id": parent_span_id,
                    "session_id": session_id,
                    "service_name": service_name,
                    "agent_name": agent_name,
                    "span_kind": sp.kind.name if sp.kind else "INTERNAL",
                    "name": sp.name,
                    "start_time_ms": start_ms,
                    "duration_ms": duration_ms,
                    "status": status_str,
                    "attributes_json": json.dumps(clean_attrs, default=str),
                }
            )
        try:
            insert_spans_sync(rows, db_path=target_db)
            return SpanExportResult.SUCCESS
        except Exception as exc:
            logger.warning("SQLiteSpanExporter failed to persist spans: %s", exc)
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        return None


def setup_telemetry(
    service_name: str = "gateway",
    db_path: Path | str | None = None,
) -> trace.Tracer:
    """Initialize OpenTelemetry TracerProvider with `SQLiteSpanExporter` (+ optional Cloud Trace)."""
    global _provider_initialized, _sqlite_exporter_instance

    current_service_name.set(service_name)
    settings = get_settings()

    if _sqlite_exporter_instance is not None:
        _sqlite_exporter_instance.set_db_path(db_path)

    if not _provider_initialized:
        existing = trace.get_tracer_provider()
        if isinstance(existing, TracerProvider):
            provider = existing
        else:
            resource = Resource.create(
                {
                    "service.name": f"procureflow-{service_name}",
                    "service.namespace": "procureflow-ai",
                }
            )
            provider = TracerProvider(resource=resource)
            trace.set_tracer_provider(provider)

        _sqlite_exporter_instance = SQLiteSpanExporter(db_path=db_path)
        provider.add_span_processor(SimpleSpanProcessor(_sqlite_exporter_instance))

        if settings.enable_cloud_trace:
            try:
                from opentelemetry.exporter.cloud_trace import (  # type: ignore[import-not-found]
                    CloudTraceSpanExporter,
                )
                from opentelemetry.sdk.trace.export import BatchSpanProcessor

                provider.add_span_processor(
                    BatchSpanProcessor(CloudTraceSpanExporter())
                )
                logger.info("Enabled Google Cloud Trace exporter for %s", service_name)
            except Exception as exc:
                logger.warning(
                    "ENABLE_CLOUD_TRACE=true but CloudTraceSpanExporter unavailable: %s",
                    exc,
                )

        _provider_initialized = True

    return trace.get_tracer(f"procureflow.{service_name}")


def get_tracer(service_name: str | None = None) -> trace.Tracer:
    """Return an OpenTelemetry tracer, ensuring the SQLite exporter is initialized."""
    svc = service_name or current_service_name.get() or "gateway"
    if not _provider_initialized:
        return setup_telemetry(svc)
    return trace.get_tracer(f"procureflow.{svc}")


def get_current_trace_id_hex() -> str:
    """Return the active 32-char hex W3C trace ID, or empty string if none."""
    sp = trace.get_current_span()
    ctx = sp.get_span_context() if sp else None
    if ctx and ctx.is_valid:
        return f"{ctx.trace_id:032x}"
    return ""


def inject_trace_headers(
    headers: MutableMapping[str, str],
    session_id: str | None = None,
) -> None:
    """Inject W3C `traceparent`, `x-procureflow-session-id`, and `A2A-Version` headers."""
    _propagator.inject(carrier=headers)
    eff_session = session_id or current_session_id.get()
    if eff_session:
        headers[SESSION_ID_HEADER] = eff_session
    if A2A_VERSION_HEADER not in headers and A2A_VERSION_HEADER.lower() not in {
        k.lower() for k in headers
    }:
        headers[A2A_VERSION_HEADER] = A2A_VERSION_VALUE


async def _httpx_request_trace_hook(request: httpx.Request) -> None:
    """HTTPX async request event hook propagating W3C `traceparent` + session ID across A2A hops."""
    carrier: dict[str, str] = {}
    inject_trace_headers(carrier)
    for key, val in carrier.items():
        if key not in request.headers:
            request.headers[key] = val
        elif key.lower() in ("traceparent", "tracestate", SESSION_ID_HEADER):
            request.headers[key] = val


def instrument_httpx_client(client: httpx.AsyncClient) -> httpx.AsyncClient:
    """Attach W3C `traceparent` + `A2A-Version` propagation hook to an existing `httpx.AsyncClient`."""
    hooks = client.event_hooks.setdefault("request", [])
    if _httpx_request_trace_hook not in hooks:
        hooks.append(_httpx_request_trace_hook)
    if A2A_VERSION_HEADER not in client.headers:
        client.headers[A2A_VERSION_HEADER] = A2A_VERSION_VALUE
    return client


def create_traced_httpx_client(
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float = 120.0,
    base_url: str | None = None,
) -> httpx.AsyncClient:
    """Create an `httpx.AsyncClient` pre-configured with W3C `traceparent` and A2A v1.0 headers."""
    kwargs: dict[str, Any] = {
        "timeout": httpx.Timeout(timeout),
        "headers": {A2A_VERSION_HEADER: A2A_VERSION_VALUE},
        "event_hooks": {"request": [_httpx_request_trace_hook]},
    }
    if transport is not None:
        kwargs["transport"] = transport
    if base_url is not None:
        kwargs["base_url"] = base_url
    return httpx.AsyncClient(**kwargs)


class A2ATraceContextMiddleware:
    """Pure ASGI middleware extracting W3C `traceparent` and `x-procureflow-session-id` on A2A servers."""

    def __init__(
        self,
        app: Any,
        *,
        service_name: str,
        agent_name: str = "",
    ) -> None:
        self.app = app
        self.service_name = service_name
        self.agent_name = agent_name

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers_dict: dict[str, str] = {}
        for raw_k, raw_v in scope.get("headers") or []:
            headers_dict[raw_k.decode("latin1").lower()] = raw_v.decode("latin1")

        path = scope.get("path", "/")
        method = scope.get("method", "POST")
        incoming_session_id = headers_dict.get(SESSION_ID_HEADER, "")

        svc_token = current_service_name.set(self.service_name)
        sess_token = (
            current_session_id.set(incoming_session_id)
            if incoming_session_id
            else None
        )

        # Skip noisy health/card polling spans unless part of an active session trace
        if path in ("/health", "/api/health") and "traceparent" not in headers_dict:
            try:
                await self.app(scope, receive, send)
            finally:
                if sess_token is not None:
                    current_session_id.reset(sess_token)
                current_service_name.reset(svc_token)
            return

        extracted_ctx = _propagator.extract(carrier=headers_dict)
        tracer = get_tracer(self.service_name)
        span_name = f"a2a.server.{self.service_name}" if path == "/" else f"http.{self.service_name}{path}"

        status_code_holder = {"code": 200}

        async def _wrapped_send(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                status_code_holder["code"] = int(message.get("status", 200))
            await send(message)

        otel_tok = otel_context.attach(extracted_ctx)
        try:
            with tracer.start_as_current_span(
                span_name,
                kind=SpanKind.SERVER,
                attributes={
                    "procureflow.service_name": self.service_name,
                    "procureflow.agent_name": self.agent_name,
                    "procureflow.session_id": incoming_session_id,
                    "http.method": method,
                    "http.target": path,
                },
            ) as span:
                try:
                    await self.app(scope, receive, _wrapped_send)
                    if status_code_holder["code"] >= 500:
                        span.set_status(
                            Status(StatusCode.ERROR, f"HTTP {status_code_holder['code']}")
                        )
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    raise
        finally:
            try:
                otel_context.detach(otel_tok)
            except ValueError:
                pass
            if sess_token is not None:
                current_session_id.reset(sess_token)
            current_service_name.reset(svc_token)
