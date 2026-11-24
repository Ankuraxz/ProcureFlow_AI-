"""Concierge Orchestrator Agent (`concierge_orchestrator`, Port 8000, `gemini-3.5-flash`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from google.adk.agents import LlmAgent, ParallelAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.agents.remote_a2a_agent import (
    AGENT_CARD_WELL_KNOWN_PATH,
    RemoteA2aAgent,
)
from google.adk.runners import Runner
from google.adk.sessions.database_session_service import DatabaseSessionService
from google.adk.tools.agent_tool import AgentTool
import httpx

from ..config import APP_NAME, get_session_db_url, get_settings
from ..observability.callbacks import build_agent_callbacks
from ..observability.tracing import (
    create_traced_httpx_client,
    instrument_httpx_client,
)
from ..tools.orchestrator_tools import (
    emit_a2ui_surface,
    recall_user_episodes,
    record_user_episode,
    request_ap2_human_signature,
)
from .skill_loader import build_lazy_skill_toolset

ORCHESTRATOR_SKILL_NAMES = ("a2ui-surface-composer",)


def _card_url_for_base(base_url: str) -> str:
    clean = base_url.rstrip("/")
    if clean.endswith(".json"):
        return clean
    return f"{clean}{AGENT_CARD_WELL_KNOWN_PATH}"


def _build_static_docker_card_if_needed(
    name: str,
    description: str,
    base_url: str,
) -> Any:
    """Allow plain HTTP internal Docker Compose service hostnames (e.g. `http://catalog:8001`).

    ADK 2.15.0's URL card fetcher enforces HTTPS on non-loopback hostnames, whereas passing a
    direct `AgentCard` leaves internal container-network HTTP targets under the caller's control.
    For `localhost` / `127.0.0.1` / `https://`, returns the standard well-known card URL string.
    """
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https" or host in ("localhost", "127.0.0.1", "::1") or host.endswith(".localhost"):
        return _card_url_for_base(base_url)

    from a2a.types import AgentCapabilities, AgentCard, AgentInterface

    rpc_target = f"{base_url.rstrip('/')}/"
    return AgentCard(
        name=name,
        description=description,
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=False),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        supported_interfaces=[
            AgentInterface(url=rpc_target, protocol_binding="JSONRPC", protocol_version="1.0")
        ],
    )


def create_remote_a2a_subagents(
    *,
    catalog_url: str | None = None,
    policy_url: str | None = None,
    settlement_url: str | None = None,
    httpx_client: httpx.AsyncClient | None = None,
    catalog_httpx_client: httpx.AsyncClient | None = None,
    policy_httpx_client: httpx.AsyncClient | None = None,
    settlement_httpx_client: httpx.AsyncClient | None = None,
) -> dict[str, RemoteA2aAgent]:
    """Create the three `RemoteA2aAgent` clients with W3C `traceparent` HTTP propagation."""
    settings = get_settings()
    cat_base = catalog_url or settings.catalog_agent_url
    pol_base = policy_url or settings.policy_agent_url
    set_base = settlement_url or settings.settlement_agent_url

    def _resolve_client(specific: httpx.AsyncClient | None) -> httpx.AsyncClient:
        if specific is not None:
            return instrument_httpx_client(specific)
        if httpx_client is not None:
            return instrument_httpx_client(httpx_client)
        return create_traced_httpx_client(timeout=120.0)

    cat_desc = (
        "Catalog & Sourcing SLM (Port 8001): searches hardware/SaaS SKUs, computes volume "
        "discounts, and checks shipping lead times. Pass user role, preferences, item, and quantity."
    )
    pol_desc = (
        "Policy & FinOps Risk SLM (Port 8002): checks live department budget utilization, "
        "audits vendor compliance tiers, and classifies AP2 governance route "
        "(AUTO_INTENT_MANDATE, HITL_CART_MANDATE, HARD_BLOCK). Pass user_id, vendor_id, and order_total_usd."
    )
    set_desc = (
        "AP2 Cryptographic Settlement Agent (Port 8003): checks standing IntentMandate, "
        "stages CartMandates for HITL signing, or executes Ed25519 PaymentMandate settlement."
    )

    catalog_remote = RemoteA2aAgent(
        name="catalog_agent",
        description=cat_desc,
        agent_card=_build_static_docker_card_if_needed("catalog_agent", cat_desc, cat_base),
        httpx_client=_resolve_client(catalog_httpx_client),
    )
    policy_remote = RemoteA2aAgent(
        name="policy_risk_agent",
        description=pol_desc,
        agent_card=_build_static_docker_card_if_needed("policy_risk_agent", pol_desc, pol_base),
        httpx_client=_resolve_client(policy_httpx_client),
    )
    settlement_remote = RemoteA2aAgent(
        name="ap2_settlement_agent",
        description=set_desc,
        agent_card=_build_static_docker_card_if_needed(
            "ap2_settlement_agent", set_desc, set_base
        ),
        httpx_client=_resolve_client(settlement_httpx_client),
    )
    return {
        "catalog_agent": catalog_remote,
        "policy_risk_agent": policy_remote,
        "ap2_settlement_agent": settlement_remote,
    }


def create_parallel_sourcing_policy_agent(
    catalog_remote: RemoteA2aAgent,
    policy_remote: RemoteA2aAgent,
) -> ParallelAgent:
    """Construct an ADK `ParallelAgent` pairing Catalog & Policy A2A sub-agents."""
    return ParallelAgent(
        name="parallel_sourcing_and_policy",
        description="Concurrent A2A fan-out across Catalog SLM and Policy & FinOps SLM.",
        sub_agents=[catalog_remote, policy_remote],
    )


def _orchestrator_instruction(ctx: ReadonlyContext) -> str:
    state = ctx.state
    user_id = state.get("user:user_id") or "maya_lin"
    full_name = state.get("user:full_name") or user_id
    role = state.get("user:role") or "Enterprise Employee"
    dept_id = state.get("user:department_id") or "dept_ml_rnd"
    dept_name = state.get("user:department_name") or "Engineering"
    auto_limit = state.get("app:ap2_auto_limit_usd") or 500.0
    warn_pct = round(float(state.get("app:budget_warning_ratio") or 0.80) * 100.0, 0)

    return (
        "You are `concierge_orchestrator`, the Enterprise Procurement & Spend Desk Concierge for ProcureFlow AI.\n"
        f"Active Employee Context:\n"
        f"- `user_id`: {user_id}\n"
        f"- `full_name`: {full_name}\n"
        f"- `role`: {role}\n"
        f"- `department_id`: {dept_id} ({dept_name})\n"
        f"- Policy Thresholds: Autonomous AP2 `IntentMandate` limit = ${float(auto_limit):.2f} USD; "
        f"FinOps Budget Warning Band >= {warn_pct:.0f}%.\n\n"
        "MANDATORY MULTI-AGENT ORCHESTRATION & PROCEDURAL PROTOCOL:\n"
        "1. Procedural Skill & Episodic Memory:\n"
        "   - Call `list_skills` and `load_skill(skill_name='a2ui-surface-composer')` so all A2UI widget schemas match exact contracts.\n"
        f"   - Call `recall_user_episodes(user_id='{user_id}')` on sourcing requests to honor saved preferences "
        "(e.g., preferred laptop brand, USB-C 4K monitors, <= 3-day shipping). If the user states a new durable preference, call `record_user_episode`.\n"
        "2. Parallel A2A Sourcing & Policy Evaluation (BEFORE any settlement):\n"
        "   - Delegate SKU discovery & volume discount pricing to `catalog_agent` (include `user_id`, role, preferences, item, and quantity in `request`).\n"
        "   - Delegate department budget & vendor compliance audit to `policy_risk_agent` (include `user_id`, `vendor_id`, and post-discount `order_total_usd` in `request`). "
        "Note: you can call `catalog_agent` and `policy_risk_agent` concurrently to inspect SKUs and live budget, and once you have the exact `vendor_id` + `subtotal_usd` from `catalog_agent`, ensure `policy_risk_agent` audits that exact `vendor_id` and `order_total_usd`!\n"
        "   - NEVER invent prices, discounts, budgets, or vendor tiers.\n"
        "3. Declarative A2UI Composition (`emit_a2ui_surface`):\n"
        "   - Whenever presenting catalog quotes/options, emit a `VendorComparisonCard` surface using the `vendor_comparison_item` fields from `catalog_agent`.\n"
        "   - Whenever evaluating budget impact or purchasing, emit a `BudgetGaugeCard` surface using the exact `budget_gauge_payload` dictionary from `policy_risk_agent`.\n"
        "4. Strict AP2 Governance Routing:\n"
        "   - `QUOTE_ONLY` (user asks for a quote/recommendation/comparison ONLY or says 'do not place an order yet'):\n"
        "     STOP after emitting `VendorComparisonCard` (and `BudgetGaugeCard` if budget was checked). Do NOT call `ap2_settlement_agent`, do NOT stage a `CartMandate`, and do NOT call `request_ap2_human_signature`!\n"
        "   - Route A — `HARD_BLOCK` (`vendor_tier == 'BLOCKED'` OR `projected_utilization_pct > 100%`):\n"
        "     Refuse settlement immediately! Do NOT call `ap2_settlement_agent`, do NOT stage a CartMandate, and NEVER emit `AP2CartMandateApprovalCard` or `OrderReceiptCard`. Explain why policy blocked the request and suggest compliant alternatives.\n"
        "   - Route B — `AUTO_INTENT_MANDATE` (user intends to order AND `total_usd <= 500.0` AND `vendor_tier in ('PREFERRED', 'APPROVED')` AND `projected_utilization_pct < 80.0%`):\n"
        f"     Call `ap2_settlement_agent` asking it to verify `{user_id}`'s standing `IntentMandate` and execute `AUTO_INTENT_MANDATE` settlement with exact `vendor_id` and `line_items_json`.\n"
        "     Then emit an `OrderReceiptCard` via `emit_a2ui_surface` using the settled `order_receipt_card_payload`!\n"
        "   - Route C — `HITL_CART_MANDATE` (user intends to order AND (`total_usd > 500.0` OR `vendor_tier == 'UNVERIFIED'` OR `80.0% <= projected_utilization_pct <= 100.0%`)):\n"
        "     NEVER settle autonomously! Instead:\n"
        f"     (i) Call `ap2_settlement_agent` to `stage_cart_mandate` for `{user_id}` with exact `vendor_id`, `line_items_json`, and `escalation_reasons_json`.\n"
        "     (ii) Call `request_ap2_human_signature(cart_mandate_id=..., summary_hint=...)` to emit the `AP2CartMandateApprovalCard` and pause for the human reviewer's cryptographic Ed25519 signature.\n"
        "5. Keep your final conversational message crisp, grounded in exact dollar amounts, discount percentages, and SKU names, and aligned with the A2UI cards you emitted."
    )


def create_orchestrator_agent(
    *,
    model: str | None = None,
    catalog_url: str | None = None,
    policy_url: str | None = None,
    settlement_url: str | None = None,
    httpx_client: httpx.AsyncClient | None = None,
    catalog_httpx_client: httpx.AsyncClient | None = None,
    policy_httpx_client: httpx.AsyncClient | None = None,
    settlement_httpx_client: httpx.AsyncClient | None = None,
) -> LlmAgent:
    """Create the root `concierge_orchestrator` `LlmAgent` wired to the 3 `RemoteA2aAgent` services."""
    settings = get_settings()
    resolved_model = model or settings.orchestrator_model
    callbacks = build_agent_callbacks(
        service_name="gateway",
        agent_name="concierge_orchestrator",
        default_model=resolved_model,
    )
    skill_toolset = build_lazy_skill_toolset(
        ORCHESTRATOR_SKILL_NAMES, settings.skills_dir
    )
    remotes = create_remote_a2a_subagents(
        catalog_url=catalog_url,
        policy_url=policy_url,
        settlement_url=settlement_url,
        httpx_client=httpx_client,
        catalog_httpx_client=catalog_httpx_client,
        policy_httpx_client=policy_httpx_client,
        settlement_httpx_client=settlement_httpx_client,
    )

    agent = LlmAgent(
        name="concierge_orchestrator",
        model=resolved_model,
        description=(
            "ProcureFlow AI Root Concierge Orchestrator (Port 8000): coordinates multi-turn "
            "enterprise procurement across Catalog, Policy & FinOps, and AP2 Settlement A2A "
            "agents, manages episodic memory, composes A2UI surfaces, and enforces HITL gates."
        ),
        instruction=_orchestrator_instruction,
        tools=[
            skill_toolset,
            recall_user_episodes,
            record_user_episode,
            AgentTool(agent=remotes["catalog_agent"]),
            AgentTool(agent=remotes["policy_risk_agent"]),
            AgentTool(agent=remotes["ap2_settlement_agent"]),
            emit_a2ui_surface,
            request_ap2_human_signature,
        ],
        before_model_callback=callbacks.before_model_callback,
        after_model_callback=callbacks.after_model_callback,
        before_tool_callback=callbacks.before_tool_callback,
        after_tool_callback=callbacks.after_tool_callback,
    )
    # Attach remote references for introspection in tests and health probes
    object.__setattr__(agent, "_remote_a2a_agents", remotes)
    return agent


def create_session_service(
    db_path: Path | str | None = None,
) -> DatabaseSessionService:
    """Create the SQLite-backed ADK `DatabaseSessionService` (`sqlite+aiosqlite:///...`)."""
    return DatabaseSessionService(db_url=get_session_db_url(db_path))


def create_orchestrator_runner(
    *,
    agent: LlmAgent | None = None,
    session_service: DatabaseSessionService | None = None,
    session_db_path: Path | str | None = None,
    httpx_client: httpx.AsyncClient | None = None,
) -> Runner:
    """Create a configured ADK `Runner` for `concierge_orchestrator`."""
    resolved_agent = agent or create_orchestrator_agent(httpx_client=httpx_client)
    resolved_sessions = session_service or create_session_service(session_db_path)
    return Runner(
        app_name=APP_NAME,
        agent=resolved_agent,
        session_service=resolved_sessions,
    )
