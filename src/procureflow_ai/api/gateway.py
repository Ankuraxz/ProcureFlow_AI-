"""FastAPI Gateway (Port 8000) serving SSE chat, A2UI surfaces, AP2 HITL signing, and FinOps telemetry."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
import os
from typing import Any, AsyncIterator
import uuid

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from google.adk.events.event import Event
from google.adk.runners import Runner
from google.adk.sessions.database_session_service import DatabaseSessionService
from google.adk.sessions.session import Session
from google.genai import types as genai_types
import httpx
from opentelemetry.trace import SpanKind, Status, StatusCode
from pydantic import BaseModel, Field, field_validator

from ..agents.orchestrator import (
    create_orchestrator_agent,
    create_orchestrator_runner,
    create_session_service,
)
from ..config import APP_NAME, get_settings
from ..db.queries import (
    append_session_ui_turn,
    compute_budget_projection,
    get_cart_mandate_record,
    get_persona_context,
    get_telemetry_summary,
    list_orders,
    list_personas_with_budgets,
    list_session_cart_mandates,
    list_session_ui_turns,
    list_telemetry_spans,
    mark_cart_mandate_rejected,
    update_cart_mandate_surface_status_in_session,
    update_persona_public_key,
    verify_and_settle_cart_mandate,
)
from ..db.seed import async_init_and_seed_db, init_and_seed_db
from ..observability.context import (
    current_service_name,
    current_session_id,
    current_sse_queue,
)
from ..observability.tracing import (
    create_traced_httpx_client,
    get_tracer,
    instrument_httpx_client,
    setup_telemetry,
)
from ..protocols.a2ui import create_a2ui_surface
from ..protocols.ap2 import AP2VerificationError, verify_stored_cart_mandate

logger = logging.getLogger(__name__)

REQUEST_CONFIRMATION_FC_NAME = "adk_request_confirmation"


class _TurnRecordingSSEQueue(asyncio.Queue[dict[str, Any] | None]):
    """SSE queue that also records every streamed A2UI surface and HITL mandate for persistence."""

    def __init__(self) -> None:
        super().__init__()
        self.recorded_surfaces: list[dict[str, Any]] = []
        self.seen_surface_ids: set[str] = set()
        self.hitl_mandate: dict[str, Any] | None = None

    def record_surface(self, surface: dict[str, Any]) -> None:
        if not isinstance(surface, dict) or not surface.get("surface_type"):
            return
        sid = str(surface.get("surface_id") or "")
        if sid and sid in self.seen_surface_ids:
            return
        if sid:
            self.seen_surface_ids.add(sid)
        self.recorded_surfaces.append(surface)

    def _put(self, item: dict[str, Any] | None) -> None:
        if isinstance(item, dict):
            if item.get("type") == "a2ui_surface" and isinstance(
                item.get("surface"), dict
            ):
                self.record_surface(item["surface"])
            elif item.get("type") == "hitl_required" and isinstance(
                item.get("cart_mandate"), dict
            ):
                self.hitl_mandate = item["cart_mandate"]
        super()._put(item)


# ---------------------------------------------------------------------------
# Request / Response Pydantic Contracts
# ---------------------------------------------------------------------------


class RegisterKeyRequest(BaseModel):
    """WebCrypto client Ed25519 raw public key registration body."""

    public_key_b64: str = Field(..., min_length=40)


class CreateSessionRequest(BaseModel):
    """Session creation body."""

    user_id: str = Field(default="maya_lin")
    session_id: str | None = None


class ChatStreamRequest(BaseModel):
    """Conversational SSE turn body."""

    user_id: str
    session_id: str
    message: str = Field(..., min_length=1)


class MandateDecisionRequest(BaseModel):
    """Human reviewer AP2 CartMandate APPROVE / REJECT decision body."""

    user_id: str
    session_id: str = ""
    decision: str = Field(..., pattern="^(APPROVE|REJECT)$")
    signature_b64: str | None = None
    public_key_b64: str | None = None
    reason: str | None = None

    @field_validator("decision", mode="before")
    @classmethod
    def _normalize_decision(cls, v: Any) -> str:
        return str(v).strip().upper()


# ---------------------------------------------------------------------------
# Session Bootstrap & ADK Confirmation Resolution Helpers
# ---------------------------------------------------------------------------


async def ensure_adk_session(
    session_service: DatabaseSessionService,
    *,
    user_id: str,
    session_id: str | None = None,
) -> Session:
    """Fetch an existing ADK session or create one initialized with `user:` and `app:` state."""
    persona = await get_persona_context(user_id)
    if not persona:
        raise HTTPException(status_code=404, detail=f"Unknown persona '{user_id}'")

    if session_id:
        existing = await session_service.get_session(
            app_name=APP_NAME,
            user_id=user_id,
            session_id=session_id,
        )
        if existing is not None:
            return existing

    settings = get_settings()
    initial_state: dict[str, Any] = {
        "user:user_id": persona["user_id"],
        "user:full_name": persona["full_name"],
        "user:role": persona["role"],
        "user:department_id": persona["department_id"],
        "user:department_name": persona["department_name"],
        "app:ap2_auto_limit_usd": settings.ap2_auto_limit_usd,
        "app:budget_warning_ratio": settings.budget_warning_ratio,
    }
    return await session_service.create_session(
        app_name=APP_NAME,
        user_id=user_id,
        state=initial_state,
        session_id=session_id,
    )


async def _close_open_adk_confirmation_if_any(
    session_service: DatabaseSessionService,
    *,
    user_id: str,
    session_id: str,
    confirmed: bool,
    payload: dict[str, Any],
) -> None:
    """Append a matching `adk_request_confirmation` FunctionResponse so follow-up turns stay clean."""
    if not session_id:
        return
    session = await session_service.get_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id,
    )
    if session is None or not session.events:
        return

    answered_ids: set[str] = set()
    open_conf_ids: list[str] = []
    for ev in session.events:
        for fr in ev.get_function_responses():
            if fr.id and fr.name == REQUEST_CONFIRMATION_FC_NAME:
                answered_ids.add(fr.id)
        for fc in ev.get_function_calls():
            if fc.id and fc.name == REQUEST_CONFIRMATION_FC_NAME:
                open_conf_ids.append(fc.id)

    unresolved = [cid for cid in open_conf_ids if cid not in answered_ids]
    if not unresolved:
        return

    parts: list[genai_types.Part] = []
    for conf_id in unresolved:
        part = genai_types.Part.from_function_response(
            name=REQUEST_CONFIRMATION_FC_NAME,
            response={
                "confirmed": confirmed,
                "payload": payload,
            },
        )
        if part.function_response is not None:
            part.function_response.id = conf_id
        parts.append(part)

    response_event = Event(
        author="user",
        content=genai_types.Content(role="user", parts=parts),
    )
    try:
        await session_service.append_event(session=session, event=response_event)
    except Exception as exc:
        logger.debug("Non-fatal error closing confirmation event: %s", exc)


async def _append_assistant_event_to_adk_session(
    session_service: DatabaseSessionService,
    *,
    user_id: str,
    session_id: str,
    assistant_text: str,
) -> None:
    """Record a synthesized assistant settlement/rejection message in ADK session history."""
    if not session_id:
        return
    session = await session_service.get_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id,
    )
    if session is None:
        return
    ev = Event(
        author="concierge_orchestrator",
        content=genai_types.Content(
            role="model",
            parts=[genai_types.Part.from_text(text=assistant_text)],
        ),
    )
    try:
        await session_service.append_event(session=session, event=ev)
    except Exception as exc:
        logger.debug("Non-fatal error appending assistant event: %s", exc)


# ---------------------------------------------------------------------------
# FastAPI Factory
# ---------------------------------------------------------------------------


def create_gateway_app(
    *,
    httpx_client: httpx.AsyncClient | None = None,
    runner: Runner | None = None,
    session_service: DatabaseSessionService | None = None,
    delegate_settlement_over_a2a: bool = True,
) -> FastAPI:
    """Create the ProcureFlow AI FastAPI Gateway application."""
    settings = get_settings()

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        await async_init_and_seed_db()
        setup_telemetry(service_name="gateway")
        yield
        owned_client: httpx.AsyncClient | None = getattr(
            app.state, "_owned_httpx_client", None
        )
        if owned_client is not None:
            await owned_client.aclose()

    app = FastAPI(
        title="ProcureFlow AI — Gateway & Concierge Orchestrator",
        version="0.1.0",
        lifespan=_lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[*settings.cors_origins, "*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _get_runtime() -> tuple[Runner, DatabaseSessionService, httpx.AsyncClient]:
        """Lazily initialize Runner, DatabaseSessionService, and traced HTTPX client."""
        if getattr(app.state, "_runner", None) is None:
            init_and_seed_db()
            setup_telemetry(service_name="gateway")
            if httpx_client is not None:
                client = instrument_httpx_client(httpx_client)
                app.state._owned_httpx_client = None
            else:
                client = create_traced_httpx_client(timeout=120.0)
                app.state._owned_httpx_client = client

            sess_svc = session_service or create_session_service()
            orch_runner = runner or create_orchestrator_runner(
                agent=create_orchestrator_agent(httpx_client=client),
                session_service=sess_svc,
                httpx_client=client,
            )
            app.state._httpx_client = client
            app.state._session_service = sess_svc
            app.state._runner = orch_runner

        return (
            app.state._runner,
            app.state._session_service,
            app.state._httpx_client,
        )

    @app.get("/health")
    @app.get("/api/health")
    async def health_check() -> dict[str, Any]:
        _, _, client = _get_runtime()
        cur_settings = get_settings()
        targets = {
            "catalog_agent": cur_settings.catalog_agent_url,
            "policy_risk_agent": cur_settings.policy_agent_url,
            "ap2_settlement_agent": cur_settings.settlement_agent_url,
        }
        statuses: dict[str, Any] = {}
        for name, base_url in targets.items():
            try:
                resp = await client.get(
                    f"{base_url.rstrip('/')}/.well-known/agent-card.json",
                    timeout=2.5,
                )
                statuses[name] = {
                    "url": base_url,
                    "reachable": resp.status_code == 200,
                    "status_code": resp.status_code,
                }
            except Exception as exc:
                statuses[name] = {
                    "url": base_url,
                    "reachable": False,
                    "error": str(exc),
                }

        return {
            "status": "ok",
            "service": "gateway",
            "orchestrator_model": cur_settings.orchestrator_model,
            "slm_model": cur_settings.slm_model,
            "a2a_agents": statuses,
        }

    @app.get("/api/personas")
    async def get_personas() -> list[dict[str, Any]]:
        await async_init_and_seed_db()
        personas = await list_personas_with_budgets()
        for p in personas:
            # Provide ergonomic aliases so any frontend field naming convention works cleanly
            p["name"] = p["full_name"]
            p["current_spent_usd"] = p["spent_usd"]
        return personas

    @app.post("/api/personas/{user_id}/register-key")
    async def register_persona_key(
        user_id: str,
        body: RegisterKeyRequest,
    ) -> dict[str, Any]:
        try:
            return await update_persona_public_key(
                user_id=user_id,
                public_key_b64=body.public_key_b64,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/sessions")
    async def list_user_sessions(
        user_id: str = Query(default="maya_lin"),
    ) -> list[dict[str, Any]]:
        _, sess_svc, _ = _get_runtime()
        resp = await sess_svc.list_sessions(app_name=APP_NAME, user_id=user_id)
        out: list[dict[str, Any]] = []
        for s in resp.sessions:
            turns = await list_session_ui_turns(s.id)
            first_user_msg = next(
                (t["content"] for t in turns if t["role"] == "user" and t["content"]),
                "New Procurement Session",
            )
            out.append(
                {
                    "session_id": s.id,
                    "id": s.id,
                    "user_id": s.user_id,
                    "title": first_user_msg[:64],
                    "message_count": len(turns),
                    "last_update_time": s.last_update_time,
                }
            )
        out.sort(key=lambda item: float(item.get("last_update_time") or 0.0), reverse=True)
        return out

    @app.post("/api/sessions")
    async def create_user_session(body: CreateSessionRequest) -> dict[str, Any]:
        _, sess_svc, _ = _get_runtime()
        session = await ensure_adk_session(
            sess_svc,
            user_id=body.user_id,
            session_id=body.session_id,
        )
        return {
            "session_id": session.id,
            "id": session.id,
            "user_id": session.user_id,
            "state": dict(session.state),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    @app.get("/api/sessions/{session_id}/messages")
    async def get_session_messages(session_id: str) -> list[dict[str, Any]]:
        turns = await list_session_ui_turns(session_id)
        for t in turns:
            t["id"] = t["turn_id"]
        return turns

    @app.post("/api/chat/stream")
    async def chat_stream(body: ChatStreamRequest) -> StreamingResponse:
        orch_runner, sess_svc, _ = _get_runtime()
        await ensure_adk_session(
            sess_svc,
            user_id=body.user_id,
            session_id=body.session_id,
        )

        # Reset per-turn temp state before starting new turn
        existing_session = await sess_svc.get_session(
            app_name=APP_NAME,
            user_id=body.user_id,
            session_id=body.session_id,
        )
        if existing_session is not None and existing_session.state.get("temp:emitted_surfaces"):
            reset_ev = Event(author="user")
            reset_ev.actions.state_delta["temp:emitted_surfaces"] = []
            reset_ev.actions.state_delta["temp:hitl_cart_mandate"] = None
            try:
                await sess_svc.append_event(session=existing_session, event=reset_ev)
            except Exception:
                pass

        await append_session_ui_turn(
            session_id=body.session_id,
            user_id=body.user_id,
            role="user",
            content=body.message,
        )

        pre_turn_order_ids = {
            o["order_id"] for o in await list_orders(user_id=body.user_id)
        }
        pre_turn_mandate_ids = {
            m["cart_mandate_id"]
            for m in await list_session_cart_mandates(body.session_id)
        }

        sse_queue = _TurnRecordingSSEQueue()

        async def _run_turn() -> None:
            current_service_name.set("gateway")
            current_session_id.set(body.session_id)
            current_sse_queue.set(sse_queue)  # type: ignore[arg-type]

            tracer = get_tracer("gateway")
            final_text_parts: list[str] = []
            root_trace_id = ""

            with tracer.start_as_current_span(
                "gateway.chat_turn",
                kind=SpanKind.SERVER,
                attributes={
                    "procureflow.service_name": "gateway",
                    "procureflow.agent_name": "concierge_orchestrator",
                    "procureflow.session_id": body.session_id,
                    "procureflow.user_id": body.user_id,
                },
            ) as root_span:
                span_ctx = root_span.get_span_context()
                if span_ctx and span_ctx.is_valid:
                    root_trace_id = f"{span_ctx.trace_id:032x}"

                await sse_queue.put(
                    {
                        "type": "agent_status",
                        "agent": "concierge_orchestrator",
                        "status": "running",
                        "detail": "Orchestrating procurement workflow",
                    }
                )

                user_msg = genai_types.Content(
                    role="user",
                    parts=[genai_types.Part.from_text(text=body.message)],
                )

                try:
                    async for event in orch_runner.run_async(
                        user_id=body.user_id,
                        session_id=body.session_id,
                        new_message=user_msg,
                    ):
                        if event.actions and event.actions.state_delta:
                            for s in (
                                event.actions.state_delta.get("temp:emitted_surfaces")
                                or []
                            ):
                                if isinstance(s, dict):
                                    sse_queue.record_surface(s)
                            delta_hitl = event.actions.state_delta.get(
                                "temp:hitl_cart_mandate"
                            )
                            if isinstance(delta_hitl, dict):
                                sse_queue.hitl_mandate = delta_hitl

                        if event.author == "concierge_orchestrator" and event.content and event.content.parts:
                            for part in event.content.parts:
                                if getattr(part, "thought", False):
                                    continue
                                if part.text:
                                    final_text_parts.append(part.text)
                                    await sse_queue.put(
                                        {
                                            "type": "text_delta",
                                            "content": part.text,
                                        }
                                    )

                    # Merge any surfaces & HITL state stored on ADK session state
                    updated_sess = await sess_svc.get_session(
                        app_name=APP_NAME,
                        user_id=body.user_id,
                        session_id=body.session_id,
                    )
                    if updated_sess is not None:
                        for s in updated_sess.state.get("temp:emitted_surfaces") or []:
                            if isinstance(s, dict):
                                sse_queue.record_surface(s)
                        sess_hitl = updated_sess.state.get("temp:hitl_cart_mandate")
                        if isinstance(sess_hitl, dict) and sse_queue.hitl_mandate is None:
                            sse_queue.hitl_mandate = sess_hitl

                    # Guarantee OrderReceiptCard is emitted & persisted if a turn settled an order
                    post_orders = await list_orders(user_id=body.user_id)
                    for order in post_orders:
                        if (
                            order["order_id"] not in pre_turn_order_ids
                            and order.get("session_id") == body.session_id
                        ):
                            has_receipt_card = any(
                                s.get("surface_type") == "OrderReceiptCard"
                                and s.get("payload", {}).get("order_id")
                                == order["order_id"]
                                for s in sse_queue.recorded_surfaces
                            )
                            if not has_receipt_card:
                                auto_receipt_surface = create_a2ui_surface(
                                    surface_type="OrderReceiptCard",
                                    title=f"AP2 Settled Order Receipt — {order['order_id']}",
                                    payload=order,
                                ).model_dump()
                                await sse_queue.put(
                                    {
                                        "type": "a2ui_surface",
                                        "surface": auto_receipt_surface,
                                    }
                                )

                    # Guarantee AP2CartMandateApprovalCard is emitted & persisted if a turn staged a CartMandate
                    post_mandates = await list_session_cart_mandates(
                        body.session_id, status="PENDING_SIGNATURE"
                    )
                    for cm in post_mandates:
                        if cm["cart_mandate_id"] not in pre_turn_mandate_ids:
                            card_payload = {
                                "cart_mandate_id": cm["cart_mandate_id"],
                                "user_id": cm["user_id"],
                                "department_id": cm["department_id"],
                                "vendor_id": cm["vendor_id"],
                                "vendor_name": cm["vendor_name"],
                                "vendor_tier": cm["vendor_tier"],
                                "line_items": cm["line_items"],
                                "total_usd": float(cm["total_usd"]),
                                "currency": cm["currency"],
                                "cart_sha256": cm["cart_sha256"],
                                "canonical_payload_to_sign": cm[
                                    "canonical_payload_to_sign"
                                ],
                                "escalation_reasons": cm["escalation_reasons"],
                                "status": cm["status"],
                                "expires_at": cm["expires_at"],
                            }
                            if sse_queue.hitl_mandate is None:
                                sse_queue.hitl_mandate = card_payload
                            has_mandate_card = any(
                                s.get("surface_type") == "AP2CartMandateApprovalCard"
                                and s.get("payload", {}).get("cart_mandate_id")
                                == cm["cart_mandate_id"]
                                for s in sse_queue.recorded_surfaces
                            )
                            if not has_mandate_card:
                                auto_mandate_surface = create_a2ui_surface(
                                    surface_type="AP2CartMandateApprovalCard",
                                    title=(
                                        f"AP2 CartMandate Review — {cm['vendor_name']} "
                                        f"(${float(cm['total_usd']):,.2f})"
                                    ),
                                    payload=card_payload,
                                ).model_dump()
                                await sse_queue.put(
                                    {
                                        "type": "a2ui_surface",
                                        "surface": auto_mandate_surface,
                                    }
                                )
                                await sse_queue.put(
                                    {
                                        "type": "hitl_required",
                                        "cart_mandate": card_payload,
                                    }
                                )

                    emitted_surfaces = list(sse_queue.recorded_surfaces)
                    hitl_mandate = sse_queue.hitl_mandate

                    final_text = "".join(final_text_parts).strip()
                    if not final_text and hitl_mandate:
                        final_text = (
                            f"Order requires human approval under corporate AP2 policy "
                            f"(${float(hitl_mandate.get('total_usd', 0.0)):,.2f}). "
                            f"Please review the staged CartMandate ({hitl_mandate.get('cart_mandate_id')}) "
                            f"and sign with your Ed25519 key to authorize settlement."
                        )
                        await sse_queue.put(
                            {"type": "text_delta", "content": final_text}
                        )

                    await sse_queue.put(
                        {
                            "type": "text_done",
                            "content": final_text,
                        }
                    )
                    await sse_queue.put(
                        {
                            "type": "agent_status",
                            "agent": "concierge_orchestrator",
                            "status": "completed",
                            "detail": "Turn completed",
                        }
                    )

                    await append_session_ui_turn(
                        session_id=body.session_id,
                        user_id=body.user_id,
                        role="assistant",
                        content=final_text,
                        surfaces=emitted_surfaces,
                        hitl_mandate=hitl_mandate,
                        trace_id=root_trace_id,
                    )

                    await sse_queue.put(
                        {
                            "type": "done",
                            "session_id": body.session_id,
                            "trace_id": root_trace_id,
                        }
                    )
                except Exception as exc:
                    logger.exception("Error during chat_stream turn: %s", exc)
                    root_span.record_exception(exc)
                    root_span.set_status(Status(StatusCode.ERROR, str(exc)))
                    await sse_queue.put(
                        {
                            "type": "error",
                            "message": str(exc),
                        }
                    )
                    await sse_queue.put(
                        {
                            "type": "done",
                            "session_id": body.session_id,
                            "trace_id": root_trace_id,
                        }
                    )
                finally:
                    await sse_queue.put(None)

        async def _sse_generator() -> AsyncIterator[str]:
            producer = asyncio.create_task(_run_turn())
            emitted_surface_ids: set[str] = set()
            try:
                while True:
                    item = await sse_queue.get()
                    if item is None:
                        break
                    if item.get("type") == "a2ui_surface":
                        surf = item.get("surface")
                        if isinstance(surf, dict):
                            sse_queue.record_surface(surf)
                            sid = str(surf.get("surface_id") or "")
                            if sid in emitted_surface_ids:
                                continue
                            if sid:
                                emitted_surface_ids.add(sid)
                    yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
            finally:
                if not producer.done():
                    producer.cancel()

        return StreamingResponse(
            _sse_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/ap2/mandates/{cart_mandate_id}/decision")
    async def submit_mandate_decision(
        cart_mandate_id: str,
        body: MandateDecisionRequest,
    ) -> dict[str, Any]:
        orch_runner, sess_svc, _ = _get_runtime()
        cart = await get_cart_mandate_record(cart_mandate_id)
        if not cart:
            raise HTTPException(
                status_code=404,
                detail=f"CartMandate '{cart_mandate_id}' not found",
            )
        if cart["user_id"] != body.user_id:
            raise HTTPException(
                status_code=403,
                detail=f"CartMandate '{cart_mandate_id}' belongs to '{cart['user_id']}', not '{body.user_id}'",
            )

        eff_session_id = body.session_id or cart.get("session_id") or ""
        current_service_name.set("gateway")
        if eff_session_id:
            current_session_id.set(eff_session_id)

        tracer = get_tracer("gateway")
        with tracer.start_as_current_span(
            "gateway.ap2_mandate_decision",
            kind=SpanKind.SERVER,
            attributes={
                "procureflow.service_name": "gateway",
                "procureflow.session_id": eff_session_id,
                "procureflow.cart_mandate_id": cart_mandate_id,
                "procureflow.decision": body.decision,
            },
        ) as root_span:
            span_ctx = root_span.get_span_context()
            trace_id_hex = (
                f"{span_ctx.trace_id:032x}" if span_ctx and span_ctx.is_valid else ""
            )

            await _close_open_adk_confirmation_if_any(
                sess_svc,
                user_id=body.user_id,
                session_id=eff_session_id,
                confirmed=(body.decision == "APPROVE"),
                payload={
                    "decision": body.decision,
                    "cart_mandate_id": cart_mandate_id,
                    "signature_b64": body.signature_b64,
                    "public_key_b64": body.public_key_b64,
                    "reason": body.reason,
                },
            )

            if body.decision == "REJECT":
                rejection_reason = body.reason or "Rejected by human reviewer"
                try:
                    await mark_cart_mandate_rejected(
                        cart_mandate_id=cart_mandate_id,
                        user_id=body.user_id,
                        reason=rejection_reason,
                    )
                except AP2VerificationError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc

                await update_cart_mandate_surface_status_in_session(
                    session_id=eff_session_id,
                    cart_mandate_id=cart_mandate_id,
                    new_status="REJECTED",
                )
                msg = (
                    f"CartMandate `{cart_mandate_id}` (${float(cart['total_usd']):,.2f} with "
                    f"{cart['vendor_name']}) was rejected ({rejection_reason}). "
                    "No department funds were deducted."
                )
                if eff_session_id:
                    await append_session_ui_turn(
                        session_id=eff_session_id,
                        user_id=body.user_id,
                        role="assistant",
                        content=msg,
                        surfaces=[],
                        trace_id=trace_id_hex,
                    )
                    await _append_assistant_event_to_adk_session(
                        sess_svc,
                        user_id=body.user_id,
                        session_id=eff_session_id,
                        assistant_text=msg,
                    )
                return {
                    "status": "REJECTED",
                    "cart_mandate_id": cart_mandate_id,
                    "message": msg,
                    "assistant_message": msg,
                    "order_receipt_surface": None,
                    "budget_gauge_surface": None,
                    "surfaces": [],
                    "trace_id": trace_id_hex,
                }

            # APPROVE flow: enforce deterministic Ed25519 + SHA-256 tamper verification FIRST
            if not body.signature_b64:
                raise HTTPException(
                    status_code=400,
                    detail="signature_b64 is required when decision == 'APPROVE'",
                )

            persona = await get_persona_context(body.user_id)
            if not persona:
                raise HTTPException(status_code=404, detail=f"Unknown persona '{body.user_id}'")

            eff_pub_key = body.public_key_b64 or persona["public_key_b64"]
            if eff_pub_key != persona["public_key_b64"]:
                root_span.set_status(Status(StatusCode.ERROR, "UNTRUSTED_SIGNER_PUBLIC_KEY"))
                raise HTTPException(
                    status_code=400,
                    detail="UNTRUSTED_SIGNER_PUBLIC_KEY: public_key_b64 does not match persona's registered Ed25519 key.",
                )

            valid_sig, reason_code, _ = verify_stored_cart_mandate(
                cart_mandate_id=cart["cart_mandate_id"],
                user_id=cart["user_id"],
                department_id=cart["department_id"],
                vendor_id=cart["vendor_id"],
                currency=cart["currency"],
                total_usd=float(cart["total_usd"]),
                line_items=cart["line_items"],
                stored_cart_sha256=cart["cart_sha256"],
                stored_canonical_payload=cart["canonical_payload_to_sign"],
                signature_b64=body.signature_b64,
                public_key_b64=eff_pub_key,
            )
            if not valid_sig:
                root_span.set_status(Status(StatusCode.ERROR, reason_code))
                raise HTTPException(
                    status_code=400,
                    detail=f"AP2 cryptographic verification failed: {reason_code}",
                )

            # Delegate settlement over A2A to `ap2_settlement_agent` when live API key & server available,
            # with deterministic cryptographic settlement guarantee!
            receipt: dict[str, Any] | None = None
            if delegate_settlement_over_a2a and os.getenv("GOOGLE_API_KEY"):
                remotes = getattr(orch_runner.agent, "_remote_a2a_agents", None) or {}
                settlement_remote = remotes.get("ap2_settlement_agent")
                if settlement_remote is not None:
                    with tracer.start_as_current_span(
                        "a2a.call.ap2_settlement_agent",
                        kind=SpanKind.CLIENT,
                        attributes={
                            "procureflow.service_name": "gateway",
                            "procureflow.agent_name": "concierge_orchestrator",
                            "procureflow.session_id": eff_session_id,
                            "procureflow.tool_name": "ap2_settlement_agent",
                        },
                    ):
                        try:
                            from google.adk.sessions.in_memory_session_service import (
                                InMemorySessionService,
                            )

                            temp_sess_svc = InMemorySessionService()
                            temp_sess = await temp_sess_svc.create_session(
                                app_name="a2a_settlement_delegate",
                                user_id=body.user_id,
                            )
                            temp_runner = Runner(
                                app_name="a2a_settlement_delegate",
                                agent=settlement_remote,
                                session_service=temp_sess_svc,
                            )
                            prompt_text = (
                                f"Execute HITL_CART_MANDATE settlement for user_id='{body.user_id}', "
                                f"cart_mandate_id='{cart_mandate_id}', "
                                f"signature_b64='{body.signature_b64}', "
                                f"public_key_b64='{eff_pub_key}', "
                                f"session_id='{eff_session_id}'."
                            )
                            async for _ in temp_runner.run_async(
                                user_id=body.user_id,
                                session_id=temp_sess.id,
                                new_message=genai_types.Content(
                                    role="user",
                                    parts=[genai_types.Part.from_text(text=prompt_text)],
                                ),
                            ):
                                pass
                        except Exception as exc:
                            logger.info(
                                "A2A settlement delegation fallback to local verifier (%s)",
                                exc,
                            )

            # Ensure atomic settlement has committed (idempotent if A2A call already settled it!)
            try:
                receipt = await verify_and_settle_cart_mandate(
                    cart_mandate_id=cart_mandate_id,
                    user_id=body.user_id,
                    signature_b64=body.signature_b64,
                    public_key_b64=eff_pub_key,
                    session_id=eff_session_id,
                )
            except AP2VerificationError as exc:
                root_span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            await update_cart_mandate_surface_status_in_session(
                session_id=eff_session_id,
                cart_mandate_id=cart_mandate_id,
                new_status="SIGNED_SETTLED",
            )

            receipt_surface = create_a2ui_surface(
                surface_type="OrderReceiptCard",
                title=f"AP2 Settled Order Receipt — {receipt['order_id']}",
                payload=receipt,
            ).model_dump()

            post_budget = await compute_budget_projection(body.user_id, 0.0)
            settled_amt = round(float(receipt["total_usd"]), 2)
            budget_total = round(float(post_budget["budget_total_usd"]), 2)
            post_spent = round(float(post_budget["current_spent_usd"]), 2)
            pre_spent = round(max(0.0, post_spent - settled_amt), 2)
            pre_util = (
                round((pre_spent / budget_total) * 100.0, 2)
                if budget_total > 0
                else 0.0
            )
            budget_gauge_surface = create_a2ui_surface(
                surface_type="BudgetGaugeCard",
                title=f"{post_budget['department_name']} — Updated Post-Settlement Budget",
                payload={
                    "department_id": post_budget["department_id"],
                    "department_name": post_budget["department_name"],
                    "budget_total_usd": budget_total,
                    "current_spent_usd": pre_spent,
                    "order_amount_usd": settled_amt,
                    "projected_spent_usd": post_spent,
                    "current_utilization_pct": pre_util,
                    "projected_utilization_pct": post_budget["current_utilization_pct"],
                    "warning_threshold_pct": post_budget["warning_threshold_pct"],
                    "status": post_budget["status"],
                },
            ).model_dump()

            decision_surfaces = [receipt_surface, budget_gauge_surface]

            assistant_msg = (
                f"Ed25519 signature verified on CartMandate `{cart_mandate_id}` (`SHA-256: {receipt['cart_sha256'][:16]}...`). "
                f"Minted PaymentMandate `{receipt['payment_mandate_id']}` and settled order `{receipt['order_id']}` "
                f"for **${float(receipt['total_usd']):,.2f} USD** with **{receipt['vendor_name']}**."
            )

            if eff_session_id:
                await append_session_ui_turn(
                    session_id=eff_session_id,
                    user_id=body.user_id,
                    role="assistant",
                    content=assistant_msg,
                    surfaces=decision_surfaces,
                    trace_id=trace_id_hex,
                )
                await _append_assistant_event_to_adk_session(
                    sess_svc,
                    user_id=body.user_id,
                    session_id=eff_session_id,
                    assistant_text=assistant_msg,
                )

            return {
                "status": "SETTLED",
                "order_id": receipt["order_id"],
                "payment_mandate_id": receipt["payment_mandate_id"],
                "message": assistant_msg,
                "assistant_message": assistant_msg,
                "order_receipt_surface": receipt_surface,
                "budget_gauge_surface": budget_gauge_surface,
                "surfaces": decision_surfaces,
                "receipt": receipt,
                "trace_id": trace_id_hex,
            }

    @app.get("/api/orders")
    async def get_orders(user_id: str | None = Query(default=None)) -> list[dict[str, Any]]:
        return await list_orders(user_id=user_id)

    @app.get("/api/telemetry/summary")
    async def telemetry_summary(
        session_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        return await get_telemetry_summary(session_id=session_id)

    @app.get("/api/telemetry/spans")
    async def telemetry_spans(
        session_id: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        return await list_telemetry_spans(session_id=session_id, limit=limit)

    @app.post("/api/demo/reset")
    async def reset_demo_state() -> dict[str, Any]:
        await async_init_and_seed_db(reset=True)
        personas = await list_personas_with_budgets()
        for p in personas:
            p["name"] = p["full_name"]
            p["current_spent_usd"] = p["spent_usd"]
        return {
            "status": "reset",
            "id": uuid.uuid4().hex[:8],
            "personas": personas,
        }

    return app


app = create_gateway_app()
