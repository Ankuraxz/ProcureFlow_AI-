"""Zero-socket ASGI tests for A2A AgentCards (ports 8001-8003) and FastAPI Gateway REST + SSE endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator
import json
from pathlib import Path
from typing import Any

from google.adk.a2a._compat import parse_agent_card
from google.adk.events.event import Event
from google.adk.sessions.database_session_service import DatabaseSessionService
from google.genai import types as genai_types
import httpx
import pytest

from procureflow_ai.agents.orchestrator import create_session_service
from procureflow_ai.api.gateway import create_gateway_app
from procureflow_ai.config import APP_NAME
from procureflow_ai.db.queries import get_persona_context, record_token_usage
from procureflow_ai.observability.context import push_sse_event
from procureflow_ai.protocols.a2ui import create_a2ui_surface
from procureflow_ai.protocols.ap2 import generate_ed25519_keypair, sign_ed25519
from procureflow_ai.tools.settlement_tools import stage_cart_mandate


@pytest.mark.asyncio
async def test_a2a_subservers_serve_valid_agent_cards_inprocess(
    a2a_httpx_client: httpx.AsyncClient,
) -> None:
    """Verify /.well-known/agent.json and /.well-known/agent-card.json for all 3 A2A servers."""
    expected_agents = [
        (8001, "catalog_agent"),
        (8002, "policy_risk_agent"),
        (8003, "ap2_settlement_agent"),
    ]

    for port, expected_name in expected_agents:
        for well_known in ("/.well-known/agent.json", "/.well-known/agent-card.json"):
            resp = await a2a_httpx_client.get(f"http://localhost:{port}{well_known}")
            assert resp.status_code == 200
            card = parse_agent_card(resp.json())
            assert card.name == expected_name
            assert len(card.description) > 15

        health_resp = await a2a_httpx_client.get(f"http://localhost:{port}/health")
        assert health_resp.status_code == 200
        assert health_resp.json()["agent"] == expected_name


@pytest.mark.asyncio
async def test_gateway_health_personas_key_registration_and_sessions(
    gateway_client: httpx.AsyncClient,
) -> None:
    """Verify Gateway /api/health, /api/personas, register-key, and SQLite session lifecycle."""
    health = await gateway_client.get("/api/health")
    assert health.status_code == 200
    health_data = health.json()
    assert health_data["status"] == "ok"
    for sub_name in ("catalog_agent", "policy_risk_agent", "ap2_settlement_agent"):
        assert health_data["a2a_agents"][sub_name]["reachable"] is True

    personas_resp = await gateway_client.get("/api/personas")
    assert personas_resp.status_code == 200
    personas = personas_resp.json()
    assert len(personas) == 3
    assert {p["user_id"] for p in personas} == {"maya_lin", "alex_rivera", "sam_chen"}

    # Register a fresh raw 32-byte Ed25519 public key for sam_chen
    _, new_pub_b64 = generate_ed25519_keypair()
    reg_ok = await gateway_client.post(
        "/api/personas/sam_chen/register-key",
        json={"public_key_b64": new_pub_b64},
    )
    assert reg_ok.status_code == 200
    assert reg_ok.json()["public_key_b64"] == new_pub_b64

    # Invalid key length -> 400; unknown persona -> 404
    bad_key_b64 = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    reg_bad = await gateway_client.post(
        "/api/personas/sam_chen/register-key",
        json={"public_key_b64": bad_key_b64},
    )
    assert reg_bad.status_code == 400

    reg_unknown = await gateway_client.post(
        "/api/personas/nobody_user/register-key",
        json={"public_key_b64": new_pub_b64},
    )
    assert reg_unknown.status_code == 404

    # Create and list ADK SQLite session
    sess_resp = await gateway_client.post(
        "/api/sessions",
        json={"user_id": "maya_lin", "session_id": "sess_gw_001"},
    )
    assert sess_resp.status_code == 200
    sess_json = sess_resp.json()
    assert sess_json["session_id"] == "sess_gw_001"
    assert sess_json["state"]["user:department_id"] == "dept_ml_rnd"

    listed = await gateway_client.get("/api/sessions", params={"user_id": "maya_lin"})
    assert listed.status_code == 200
    assert any(s["session_id"] == "sess_gw_001" for s in listed.json())


@pytest.mark.asyncio
async def test_gateway_ap2_mandate_approve_reject_telemetry_and_demo_reset(
    gateway_client: httpx.AsyncClient,
    domain_db_path: Path,
) -> None:
    """Verify Gateway AP2 mandate APPROVE + REJECT decisions, telemetry endpoints, and /api/demo/reset."""
    await gateway_client.post(
        "/api/sessions",
        json={"user_id": "maya_lin", "session_id": "sess_ap2_gw"},
    )
    persona = await get_persona_context("maya_lin", db_path=domain_db_path)
    assert persona is not None

    # 1. Stage CartMandate #1 and APPROVE with valid Ed25519 signature
    staged_approve = await stage_cart_mandate(
        user_id="maya_lin",
        vendor_id="vnd_apple_ent",
        line_items_json=json.dumps(
            [
                {
                    "sku_id": "sku_mbp14_m4",
                    "name": "Apple MacBook Pro 14-inch (M4 Pro, 24GB, 1TB)",
                    "quantity": 1,
                    "unit_price_usd": 2399.0,
                    "discount_pct": 0.0,
                    "line_total_usd": 2399.0,
                }
            ]
        ),
        escalation_reasons_json='["ORDER_OVER_500_AUTO_LIMIT"]',
        session_id="sess_ap2_gw",
    )
    cm_approve = staged_approve["cart_mandate"]
    valid_sig = sign_ed25519(
        persona["default_private_key_b64"],
        cm_approve["canonical_payload_to_sign"],
    )

    # Wrong user_id -> 403
    wrong_user = await gateway_client.post(
        f"/api/ap2/mandates/{cm_approve['cart_mandate_id']}/decision",
        json={
            "user_id": "sam_chen",
            "session_id": "sess_ap2_gw",
            "decision": "APPROVE",
            "signature_b64": valid_sig,
        },
    )
    assert wrong_user.status_code == 403

    # Valid signature -> 200 SETTLED + OrderReceiptCard surface + budget deducted
    approve_resp = await gateway_client.post(
        f"/api/ap2/mandates/{cm_approve['cart_mandate_id']}/decision",
        json={
            "user_id": "maya_lin",
            "session_id": "sess_ap2_gw",
            "decision": "approve",
            "signature_b64": valid_sig,
            "public_key_b64": persona["public_key_b64"],
        },
    )
    assert approve_resp.status_code == 200
    approve_data = approve_resp.json()
    assert approve_data["status"] == "SETTLED"
    assert approve_data["message"] == approve_data["assistant_message"]
    assert approve_data["order_receipt_surface"]["surface_type"] == "OrderReceiptCard"
    assert approve_data["budget_gauge_surface"]["surface_type"] == "BudgetGaugeCard"
    assert approve_data["surfaces"][0]["surface_type"] == "OrderReceiptCard"
    assert approve_data["receipt"]["total_usd"] == 2399.0

    # 2. Stage CartMandate #2 and REJECT (lowercase from UI) -> zero additional budget deducted
    staged_reject = await stage_cart_mandate(
        user_id="maya_lin",
        vendor_id="vnd_quickgear",
        line_items_json=json.dumps(
            [
                {
                    "sku_id": "sku_quickgear_dock",
                    "name": "QuickGear USB-C Dock (12-in-1 Dual 4K)",
                    "quantity": 2,
                    "unit_price_usd": 149.0,
                    "discount_pct": 0.0,
                    "line_total_usd": 298.0,
                }
            ]
        ),
        escalation_reasons_json='["UNVERIFIED_VENDOR_TIER"]',
        session_id="sess_ap2_gw",
    )
    cm_reject = staged_reject["cart_mandate"]
    reject_resp = await gateway_client.post(
        f"/api/ap2/mandates/{cm_reject['cart_mandate_id']}/decision",
        json={
            "user_id": "maya_lin",
            "session_id": "sess_ap2_gw",
            "decision": "reject",
            "reason": "Switching to certified Dell Thunderbolt dock",
        },
    )
    assert reject_resp.status_code == 200
    reject_data = reject_resp.json()
    assert reject_data["status"] == "REJECTED"
    assert reject_data["message"] == reject_data["assistant_message"]

    orders_resp = await gateway_client.get("/api/orders", params={"user_id": "maya_lin"})
    assert orders_resp.status_code == 200
    assert len(orders_resp.json()) == 1

    # 3. Telemetry summary & spans endpoints
    await record_token_usage(
        session_id="sess_ap2_gw",
        trace_id="1" * 32,
        span_id="2" * 16,
        service_name="gateway",
        agent_name="concierge_orchestrator",
        model_name="gemini-3.5-flash",
        prompt_tokens=900,
        candidates_tokens=200,
        cached_tokens=0,
        total_tokens=1100,
        estimated_cost_usd=0.00077,
        latency_ms=340.0,
        db_path=domain_db_path,
    )
    tel_sum = await gateway_client.get(
        "/api/telemetry/summary",
        params={"session_id": "sess_ap2_gw"},
    )
    assert tel_sum.status_code == 200
    assert tel_sum.json()["total_tokens"] == 1100

    tel_spans = await gateway_client.get(
        "/api/telemetry/spans",
        params={"session_id": "sess_ap2_gw"},
    )
    assert tel_spans.status_code == 200
    assert any(s["name"] == "gateway.ap2_mandate_decision" for s in tel_spans.json())

    # 4. Reset demo ledger and confirm orders cleared & budget restored to $6,500
    reset_resp = await gateway_client.post("/api/demo/reset")
    assert reset_resp.status_code == 200
    assert reset_resp.json()["status"] == "reset"
    maya_reset = next(
        p for p in reset_resp.json()["personas"] if p["user_id"] == "maya_lin"
    )
    assert maya_reset["spent_usd"] == 6500.0
    assert (await gateway_client.get("/api/orders")).json() == []


class _DeterministicMockRunner:
    """Mock ADK Runner emitting deterministic skill, A2UI surface, and text events for SSE verification."""

    def __init__(self, session_service: DatabaseSessionService) -> None:
        self._session_service = session_service
        self.agent = None

    async def run_async(
        self,
        *,
        user_id: str,
        session_id: str,
        new_message: genai_types.Content,
    ) -> AsyncIterator[Event]:
        del new_message
        push_sse_event(
            {
                "type": "skill_activated",
                "agent": "concierge_orchestrator",
                "skill_name": "a2ui-surface-composer",
            }
        )
        surface = create_a2ui_surface(
            surface_type="VendorComparisonCard",
            title="YubiKey 5C NFC Quote",
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
                        "specs": "FIDO2 USB-C + NFC",
                        "recommended": True,
                    }
                ],
                "currency": "USD",
                "notes": "5% volume discount applied.",
            },
        ).model_dump()
        push_sse_event({"type": "a2ui_surface", "surface": surface})

        sess = await self._session_service.get_session(
            app_name=APP_NAME,
            user_id=user_id,
            session_id=session_id,
        )
        if sess is not None:
            state_ev = Event(author="concierge_orchestrator")
            state_ev.actions.state_delta["temp:emitted_surfaces"] = [surface]
            await self._session_service.append_event(session=sess, event=state_ev)

        yield Event(
            author="concierge_orchestrator",
            content=genai_types.Content(
                role="model",
                parts=[
                    genai_types.Part.from_text(
                        text="Quoted 3 YubiKey 5C NFC keys from Yubico Direct at $156.75 USD."
                    )
                ],
            ),
        )


@pytest.mark.asyncio
async def test_gateway_chat_stream_sse_framing_and_message_history(
    a2a_httpx_client: httpx.AsyncClient,
    session_db_path: Path,
) -> None:
    """Verify POST /api/chat/stream SSE event framing and persisted UI turn reload."""
    sess_svc = create_session_service(session_db_path)
    mock_runner: Any = _DeterministicMockRunner(sess_svc)
    mock_app = create_gateway_app(
        httpx_client=a2a_httpx_client,
        runner=mock_runner,
        session_service=sess_svc,
        delegate_settlement_over_a2a=False,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mock_app),
        base_url="http://testserver",
    ) as client:
        events: list[dict[str, Any]] = []
        async with client.stream(
            "POST",
            "/api/chat/stream",
            json={
                "user_id": "sam_chen",
                "session_id": "sess_sse_stream_01",
                "message": "Quote 3 YubiKey 5C NFC keys",
            },
        ) as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")
            raw_body = (await resp.aread()).decode("utf-8")

        for block in raw_body.split("\n\n"):
            line = block.strip()
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))

        event_types = [e["type"] for e in events]
        assert "agent_status" in event_types
        assert "skill_activated" in event_types
        assert "a2ui_surface" in event_types
        assert "text_delta" in event_types
        assert "text_done" in event_types
        assert event_types[-1] == "done"

        text_done = next(e for e in events if e["type"] == "text_done")
        assert "$156.75" in text_done["content"]

        # Verify persisted session UI turns reload cleanly
        history_resp = await client.get("/api/sessions/sess_sse_stream_01/messages")
        assert history_resp.status_code == 200
        turns = history_resp.json()
        assert len(turns) == 2
        assert turns[0]["role"] == "user"
        assert turns[1]["role"] == "assistant"
        assert turns[1]["surfaces"][0]["surface_type"] == "VendorComparisonCard"
