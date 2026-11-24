"""ADK model and tool lifecycle callbacks for token FinOps, skill activations, and OTel spans."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import time
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from ..config import estimate_token_cost_usd
from ..db.queries import (
    get_max_skill_activation_id,
    get_recent_skill_activations_since,
    record_skill_activation,
    record_token_usage,
)
from .context import current_service_name, current_session_id, push_sse_event
from .tracing import get_tracer

logger = logging.getLogger(__name__)

A2A_SUBAGENT_NAMES = frozenset(
    {"catalog_agent", "policy_risk_agent", "ap2_settlement_agent"}
)


@dataclass
class AgentCallbackBundle:
    """Typed container of all four ADK lifecycle callbacks for an LlmAgent."""

    before_model_callback: Any
    after_model_callback: Any
    before_tool_callback: Any
    after_tool_callback: Any


def _resolve_session_id_from_ctx(ctx: CallbackContext | ToolContext) -> str:
    """Resolve the unified ProcureFlow session ID across local Runner and A2A sub-servers."""
    # Prefer propagated x-procureflow-session-id if running inside a remote A2A sub-server
    propagated = current_session_id.get()
    if propagated:
        return propagated
    try:
        sess = getattr(ctx, "session", None)
        if sess and getattr(sess, "id", None):
            return str(sess.id)
    except Exception:
        pass
    return ""


def build_agent_callbacks(
    *,
    service_name: str,
    agent_name: str,
    default_model: str,
) -> AgentCallbackBundle:
    """Create ADK 2.15.0 keyword-compatible model & tool telemetry callbacks."""
    model_timings: dict[str, tuple[float, str]] = {}
    tool_inflight: dict[str, tuple[float, Any, Any, int]] = {}

    async def before_model_callback(
        callback_context: CallbackContext,
        llm_request: LlmRequest,
        **kwargs: Any,
    ) -> LlmResponse | None:
        del kwargs
        session_id = _resolve_session_id_from_ctx(callback_context)
        if session_id and not current_session_id.get():
            current_session_id.set(session_id)
        current_service_name.set(service_name)

        req_model = getattr(llm_request, "model", None) or default_model
        inv_key = f"{getattr(callback_context, 'invocation_id', 'inv')}:{agent_name}"
        model_timings[inv_key] = (time.perf_counter(), str(req_model))
        return None

    async def after_model_callback(
        callback_context: CallbackContext,
        llm_response: LlmResponse,
        **kwargs: Any,
    ) -> LlmResponse | None:
        del kwargs
        # Skip streaming partial chunks; usage_metadata is finalized on the non-partial chunk
        if getattr(llm_response, "partial", False):
            return None

        inv_key = f"{getattr(callback_context, 'invocation_id', 'inv')}:{agent_name}"
        started, req_model = model_timings.pop(
            inv_key, (time.perf_counter(), default_model)
        )
        latency_ms = max(0.0, (time.perf_counter() - started) * 1000.0)
        session_id = _resolve_session_id_from_ctx(callback_context)

        usage = getattr(llm_response, "usage_metadata", None)
        prompt_tokens = int(getattr(usage, "prompt_token_count", 0) or 0) if usage else 0
        candidates_tokens = (
            int(getattr(usage, "candidates_token_count", 0) or 0) if usage else 0
        )
        cached_tokens = (
            int(getattr(usage, "cached_content_token_count", 0) or 0) if usage else 0
        )
        total_tokens = (
            int(getattr(usage, "total_token_count", 0) or 0)
            if usage
            else (prompt_tokens + candidates_tokens)
        )
        if total_tokens == 0 and (prompt_tokens or candidates_tokens):
            total_tokens = prompt_tokens + candidates_tokens

        model_name = str(
            getattr(llm_response, "model_version", None) or req_model or default_model
        )
        est_cost = estimate_token_cost_usd(
            model_name=model_name,
            prompt_tokens=prompt_tokens,
            candidates_tokens=candidates_tokens,
            cached_tokens=cached_tokens,
        )

        tracer = get_tracer(service_name)
        now_ns = time.time_ns()
        start_ns = max(0, now_ns - int(latency_ms * 1_000_000))
        span = tracer.start_span(
            f"llm.{agent_name}",
            kind=SpanKind.CLIENT,
            start_time=start_ns,
            attributes={
                "procureflow.service_name": service_name,
                "procureflow.agent_name": agent_name,
                "procureflow.session_id": session_id,
                "gen_ai.system": "gemini",
                "gen_ai.request.model": model_name,
                "gen_ai.usage.input_tokens": prompt_tokens,
                "gen_ai.usage.output_tokens": candidates_tokens,
                "gen_ai.usage.cached_tokens": cached_tokens,
                "gen_ai.usage.total_tokens": total_tokens,
                "procureflow.estimated_cost_usd": est_cost,
            },
        )
        span_ctx = span.get_span_context()
        trace_id_hex = f"{span_ctx.trace_id:032x}" if span_ctx and span_ctx.is_valid else ""
        span_id_hex = f"{span_ctx.span_id:016x}" if span_ctx and span_ctx.is_valid else ""
        if getattr(llm_response, "error_code", None):
            span.set_status(
                Status(StatusCode.ERROR, str(getattr(llm_response, "error_message", "")))
            )
        span.end(end_time=now_ns)

        if usage is not None or total_tokens > 0:
            try:
                await record_token_usage(
                    session_id=session_id,
                    trace_id=trace_id_hex,
                    span_id=span_id_hex,
                    service_name=service_name,
                    agent_name=agent_name,
                    model_name=model_name,
                    prompt_tokens=prompt_tokens,
                    candidates_tokens=candidates_tokens,
                    cached_tokens=cached_tokens,
                    total_tokens=total_tokens,
                    estimated_cost_usd=est_cost,
                    latency_ms=latency_ms,
                )
            except Exception as exc:
                logger.warning("Failed to record token usage: %s", exc)

        return None

    async def before_tool_callback(
        tool: BaseTool,
        args: dict[str, Any],
        tool_context: ToolContext,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        del kwargs
        session_id = _resolve_session_id_from_ctx(tool_context)
        if session_id:
            current_session_id.set(session_id)
        current_service_name.set(service_name)

        tool_name = getattr(tool, "name", "unknown_tool")
        call_id = getattr(tool_context, "function_call_id", None) or f"{tool_name}:{time.time_ns()}"
        is_a2a = tool_name in A2A_SUBAGENT_NAMES
        span_name = f"a2a.call.{tool_name}" if is_a2a else f"tool.{tool_name}"

        prev_skill_max_id = 0
        if is_a2a and session_id:
            try:
                prev_skill_max_id = await get_max_skill_activation_id(session_id)
            except Exception:
                prev_skill_max_id = 0

        tracer = get_tracer(service_name)
        span = tracer.start_span(
            span_name,
            kind=SpanKind.CLIENT if is_a2a else SpanKind.INTERNAL,
            attributes={
                "procureflow.service_name": service_name,
                "procureflow.agent_name": agent_name,
                "procureflow.session_id": session_id,
                "procureflow.tool_name": tool_name,
                "procureflow.tool_args": json.dumps(args, default=str)[:1000],
            },
        )
        ctx_with_span = trace.set_span_in_context(span)
        token = otel_context.attach(ctx_with_span)
        tool_inflight[call_id] = (time.perf_counter(), span, token, prev_skill_max_id)

        if is_a2a:
            push_sse_event(
                {
                    "type": "agent_status",
                    "agent": tool_name,
                    "status": "running",
                    "detail": f"Delegating over A2A to {tool_name}",
                }
            )
        else:
            push_sse_event(
                {
                    "type": "agent_status",
                    "agent": agent_name,
                    "status": "running",
                    "detail": f"Executing tool {tool_name}",
                }
            )

        return None

    async def after_tool_callback(
        tool: BaseTool,
        args: dict[str, Any],
        tool_context: ToolContext,
        tool_response: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        del kwargs
        tool_name = getattr(tool, "name", "unknown_tool")
        call_id = getattr(tool_context, "function_call_id", None) or ""
        inflight = tool_inflight.pop(call_id, None)

        session_id = _resolve_session_id_from_ctx(tool_context)
        is_a2a = tool_name in A2A_SUBAGENT_NAMES
        prev_skill_max_id = 0
        trace_id_hex = ""

        if inflight is not None:
            started, span, token, prev_skill_max_id = inflight
            duration_ms = round((time.perf_counter() - started) * 1000.0, 2)
            span.set_attribute("procureflow.duration_ms", duration_ms)
            if isinstance(tool_response, dict) and tool_response.get("error"):
                span.set_status(Status(StatusCode.ERROR, str(tool_response.get("error"))))
            span_ctx = span.get_span_context()
            if span_ctx and span_ctx.is_valid:
                trace_id_hex = f"{span_ctx.trace_id:032x}"
            try:
                otel_context.detach(token)
            except ValueError:
                pass
            span.end()

        # Detect SkillToolset lazy SOP activation (`load_skill`)
        if tool_name == "load_skill":
            skill_name = str(args.get("skill_name") or "")
            if skill_name and not (
                isinstance(tool_response, dict) and tool_response.get("error")
            ):
                try:
                    await record_skill_activation(
                        session_id=session_id,
                        trace_id=trace_id_hex,
                        service_name=service_name,
                        agent_name=agent_name,
                        skill_name=skill_name,
                    )
                except Exception as exc:
                    logger.warning("Failed to record skill activation: %s", exc)
                push_sse_event(
                    {
                        "type": "skill_activated",
                        "agent": agent_name,
                        "skill_name": skill_name,
                    }
                )

        # Forward any skills activated inside remote A2A sub-agent processes to SSE!
        if is_a2a:
            if session_id:
                try:
                    remote_skills = await get_recent_skill_activations_since(
                        session_id, since_id=prev_skill_max_id
                    )
                    for row in remote_skills:
                        push_sse_event(
                            {
                                "type": "skill_activated",
                                "agent": row["agent_name"],
                                "skill_name": row["skill_name"],
                            }
                        )
                except Exception:
                    pass
            push_sse_event(
                {
                    "type": "agent_status",
                    "agent": tool_name,
                    "status": "completed",
                    "detail": f"Completed A2A task on {tool_name}",
                }
            )

        return None

    return AgentCallbackBundle(
        before_model_callback=before_model_callback,
        after_model_callback=after_model_callback,
        before_tool_callback=before_tool_callback,
        after_tool_callback=after_tool_callback,
    )
