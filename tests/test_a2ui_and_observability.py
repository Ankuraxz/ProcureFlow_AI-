"""Tests for declarative A2UI Pydantic schemas, OpenTelemetry SQLiteSpanExporter, and ADK FinOps callbacks."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from opentelemetry.trace import SpanKind
from pydantic import ValidationError
import pytest

from procureflow_ai.config import estimate_token_cost_usd
from procureflow_ai.db.queries import get_telemetry_summary, list_telemetry_spans
from procureflow_ai.observability.callbacks import build_agent_callbacks
from procureflow_ai.observability.context import current_session_id, current_sse_queue
from procureflow_ai.observability.tracing import get_tracer, setup_telemetry
from procureflow_ai.protocols.a2ui import A2UISurface, create_a2ui_surface
from procureflow_ai.tools.orchestrator_tools import emit_a2ui_surface


def test_all_four_a2ui_surface_schemas_validate_and_reject_malformed_payloads() -> None:
    """Validate VendorComparisonCard, BudgetGaugeCard, AP2CartMandateApprovalCard, and OrderReceiptCard."""
    s1 = create_a2ui_surface(
        surface_type="VendorComparisonCard",
        title="Security Key Quote",
        payload={
            "items": [
                {
                    "sku_id": "sku_yubikey_5c",
                    "name": "YubiKey 5C NFC Enterprise Security Key",
                    "vendor_id": "vnd_yubico",
                    "vendor_name": "Yubico Direct",
                    "vendor_tier": "PREFERRED",
                    "unit_price_usd": 55.0,
                    "quantity": 3,
                    "discount_pct": 5.0,
                    "subtotal_usd": 156.75,
                    "lead_time_days": 2,
                    "specs": "USB-C + NFC FIDO2",
                    "recommended": True,
                }
            ],
            "currency": "USD",
            "notes": "5% volume discount applied.",
        },
    )
    assert s1.surface_type == "VendorComparisonCard"
    assert s1.payload["items"][0]["subtotal_usd"] == 156.75

    s2 = create_a2ui_surface(
        surface_type="BudgetGaugeCard",
        title="IT Ops Q4 Budget",
        payload={
            "department_id": "dept_it_ops",
            "department_name": "IT Operations",
            "budget_total_usd": 10000.0,
            "current_spent_usd": 7850.0,
            "order_amount_usd": 282.15,
            "projected_spent_usd": 8132.15,
            "current_utilization_pct": 78.5,
            "projected_utilization_pct": 81.32,
            "warning_threshold_pct": 80.0,
            "status": "WARNING_OVER_80",
        },
    )
    assert s2.payload["status"] == "WARNING_OVER_80"

    s3 = create_a2ui_surface(
        surface_type="AP2CartMandateApprovalCard",
        title="AP2 CartMandate Review",
        payload={
            "cart_mandate_id": "cm_abc123",
            "user_id": "maya_lin",
            "department_id": "dept_ml_rnd",
            "vendor_id": "vnd_apple_ent",
            "vendor_name": "Apple Enterprise Direct",
            "vendor_tier": "PREFERRED",
            "line_items": [
                {
                    "sku_id": "sku_mbp14_m4",
                    "name": "Apple MacBook Pro 14-inch",
                    "quantity": 2,
                    "unit_price_usd": 2399.0,
                    "discount_pct": 0.0,
                    "line_total_usd": 4798.0,
                }
            ],
            "total_usd": 4798.0,
            "currency": "USD",
            "cart_sha256": "a" * 64,
            "canonical_payload_to_sign": '{"cart_mandate_id":"cm_abc123"}',
            "escalation_reasons": ["ORDER_OVER_500_AUTO_LIMIT"],
            "status": "PENDING_SIGNATURE",
            "expires_at": "2026-12-31T00:00:00+00:00",
        },
    )
    assert s3.payload["cart_mandate_id"] == "cm_abc123"

    s4 = create_a2ui_surface(
        surface_type="OrderReceiptCard",
        title="Settled Receipt",
        payload={
            "order_id": "ord_xyz789",
            "status": "SETTLED",
            "authorization_mode": "AUTO_INTENT_MANDATE",
            "vendor_name": "Yubico Direct",
            "department_name": "Information Security",
            "total_usd": 156.75,
            "line_items": [
                {
                    "sku_id": "sku_yubikey_5c",
                    "name": "YubiKey 5C NFC",
                    "quantity": 3,
                    "unit_price_usd": 55.0,
                    "discount_pct": 5.0,
                    "line_total_usd": 156.75,
                }
            ],
            "intent_mandate_id": "im_default_sam_chen",
            "cart_mandate_id": None,
            "payment_mandate_id": "pm_xyz789",
            "cart_sha256": "b" * 64,
            "signature_preview": "abcd...ef01",
            "estimated_delivery_days": 2,
            "created_at": "2026-11-24T12:00:00+00:00",
        },
    )
    assert s4.payload["order_id"] == "ord_xyz789"

    # Negative validation tests
    with pytest.raises(ValidationError):
        create_a2ui_surface("VendorComparisonCard", "Empty", {"items": []})

    with pytest.raises(ValidationError):
        create_a2ui_surface(
            "BudgetGaugeCard",
            "Bad Status",
            {**s2.payload, "status": "UNKNOWN_BAND"},
        )

    with pytest.raises(ValidationError):
        A2UISurface.model_validate(
            {
                "surface_type": "OrderReceiptCard",
                "title": "Extra Forbidden Key",
                "payload": s4.payload,
                "unexpected_top_level": True,
            }
        )


@pytest.mark.asyncio
async def test_emit_a2ui_surface_tool_streams_sse_and_handles_invalid_json() -> None:
    """Verify emit_a2ui_surface appends to session temp state and pushes to SSE queue."""
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    current_sse_queue.set(queue)
    fake_ctx: Any = SimpleNamespace(state={})

    ok = await emit_a2ui_surface(
        surface_type="BudgetGaugeCard",
        title="ML R&D Budget",
        payload_json=json.dumps(
            {
                "department_id": "dept_ml_rnd",
                "department_name": "ML Research & Dev",
                "budget_total_usd": 25000.0,
                "current_spent_usd": 6500.0,
                "order_amount_usd": 500.0,
                "projected_spent_usd": 7000.0,
                "current_utilization_pct": 26.0,
                "projected_utilization_pct": 28.0,
                "warning_threshold_pct": 80.0,
                "status": "HEALTHY",
            }
        ),
        tool_context=fake_ctx,
    )
    assert ok["status"] == "emitted"
    assert len(fake_ctx.state["temp:emitted_surfaces"]) == 1
    sse_event = queue.get_nowait()
    assert sse_event["type"] == "a2ui_surface"
    assert sse_event["surface"]["surface_type"] == "BudgetGaugeCard"

    bad = await emit_a2ui_surface(
        surface_type="VendorComparisonCard",
        title="Broken",
        payload_json='{"items": []}',
        tool_context=fake_ctx,
    )
    assert bad["status"] == "INVALID_PAYLOAD"


def test_estimate_token_cost_usd_finops_pricing() -> None:
    """Verify model-tier FinOps USD token cost calculations including cached prompt discount."""
    # gemini-3.5-flash: input=$0.30/1M, cached=$0.075/1M, output=$2.50/1M
    # 10,000 prompt (4,000 cached -> 6,000 billable) + 2,000 output:
    # 6000*0.30/1e6 ($0.0018) + 4000*0.075/1e6 ($0.0003) + 2000*2.50/1e6 ($0.0050) = $0.0071
    cost_flash = estimate_token_cost_usd(
        model_name="models/gemini-3.5-flash",
        prompt_tokens=10_000,
        candidates_tokens=2_000,
        cached_tokens=4_000,
    )
    assert cost_flash == pytest.approx(0.0071, abs=1e-8)

    # gemini-3.5-flash-lite: input=$0.10/1M, cached=$0.025/1M, output=$0.40/1M
    cost_lite = estimate_token_cost_usd(
        model_name="gemini-3.5-flash-lite",
        prompt_tokens=10_000,
        candidates_tokens=2_000,
        cached_tokens=0,
    )
    assert cost_lite == pytest.approx(0.0018, abs=1e-8)


@pytest.mark.asyncio
async def test_sqlite_span_exporter_and_adk_lifecycle_callbacks(
    domain_db_path: Path,
) -> None:
    """Verify SQLiteSpanExporter + ADK model/tool callbacks record spans, tokens, cost, and skills."""
    session_id = "sess_telemetry_001"
    current_session_id.set(session_id)
    sse_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    current_sse_queue.set(sse_queue)

    tracer = setup_telemetry(service_name="catalog", db_path=domain_db_path)
    with tracer.start_as_current_span(
        "test.root_span",
        kind=SpanKind.SERVER,
        attributes={"procureflow.session_id": session_id},
    ):
        callbacks = build_agent_callbacks(
            service_name="catalog",
            agent_name="catalog_agent",
            default_model="gemini-3.5-flash-lite",
        )

        cb_ctx: Any = SimpleNamespace(
            invocation_id="inv_001",
            session=SimpleNamespace(id=session_id),
        )
        llm_req: Any = SimpleNamespace(model="gemini-3.5-flash-lite")

        await callbacks.before_model_callback(
            callback_context=cb_ctx,
            llm_request=llm_req,
        )

        # Streaming partial chunk should be ignored by token ledger
        partial_resp: Any = SimpleNamespace(partial=True, usage_metadata=None)
        await callbacks.after_model_callback(
            callback_context=cb_ctx,
            llm_response=partial_resp,
        )

        # Finalized model chunk records tokens + span
        final_resp: Any = SimpleNamespace(
            partial=False,
            model_version="gemini-3.5-flash-lite",
            error_code=None,
            usage_metadata=SimpleNamespace(
                prompt_token_count=1200,
                candidates_token_count=300,
                cached_content_token_count=200,
                total_token_count=1500,
            ),
        )
        await callbacks.after_model_callback(
            callback_context=cb_ctx,
            llm_response=final_resp,
        )

        # Simulate SkillToolset load_skill tool activation
        load_skill_tool: Any = SimpleNamespace(name="load_skill")
        tool_ctx_skill: Any = SimpleNamespace(
            function_call_id="fc_skill_1",
            session=SimpleNamespace(id=session_id),
        )
        skill_args = {"skill_name": "hardware-sourcing-sop"}
        await callbacks.before_tool_callback(
            tool=load_skill_tool,
            args=skill_args,
            tool_context=tool_ctx_skill,
        )
        await callbacks.after_tool_callback(
            tool=load_skill_tool,
            args=skill_args,
            tool_context=tool_ctx_skill,
            tool_response={"instructions": "# Hardware Sourcing SOP"},
        )

        # Simulate A2A remote agent call span from orchestrator
        orch_cb = build_agent_callbacks(
            service_name="gateway",
            agent_name="concierge_orchestrator",
            default_model="gemini-3.5-flash",
        )
        a2a_tool: Any = SimpleNamespace(name="catalog_agent")
        tool_ctx_a2a: Any = SimpleNamespace(
            function_call_id="fc_a2a_1",
            session=SimpleNamespace(id=session_id),
        )
        await orch_cb.before_tool_callback(
            tool=a2a_tool,
            args={"request": "Quote 3 YubiKeys"},
            tool_context=tool_ctx_a2a,
        )
        await orch_cb.after_tool_callback(
            tool=a2a_tool,
            args={"request": "Quote 3 YubiKeys"},
            tool_context=tool_ctx_a2a,
            tool_response={"result": "ok"},
        )

    # Verify aggregated telemetry summary and waterfall spans in SQLite
    summary = await get_telemetry_summary(session_id=session_id, db_path=domain_db_path)
    assert summary["llm_calls"] == 1
    assert summary["prompt_tokens"] == 1200
    assert summary["candidates_tokens"] == 300
    assert summary["cached_tokens"] == 200
    assert summary["total_tokens"] == 1500
    assert summary["estimated_cost_usd"] > 0.0
    assert summary["tool_calls_count"] >= 1
    assert summary["a2a_calls_count"] >= 1
    assert any(
        s["skill_name"] == "hardware-sourcing-sop" and s["agent_name"] == "catalog_agent"
        for s in summary["activated_skills"]
    )

    spans = await list_telemetry_spans(session_id=session_id, db_path=domain_db_path)
    span_names = {sp["name"] for sp in spans}
    assert "test.root_span" in span_names
    assert "llm.catalog_agent" in span_names
    assert "tool.load_skill" in span_names
    assert "a2a.call.catalog_agent" in span_names

    # Verify tracer helper stays bound
    assert get_tracer("gateway") is not None
