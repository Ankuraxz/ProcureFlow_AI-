"""Policy & FinOps Risk Specialized SLM Agent (`policy_risk_agent`, Port 8002, `gemini-3.5-flash-lite`)."""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext

from ..config import get_settings
from ..observability.callbacks import build_agent_callbacks
from ..tools.policy_tools import (
    audit_vendor_and_order_policy,
    get_department_budget_status,
)
from .skill_loader import build_lazy_skill_toolset

POLICY_SKILL_NAMES = ("procurement-policy-audit", "ap2-hitl-escalation-rules")


def _policy_instruction(_ctx: ReadonlyContext) -> str:
    return (
        "You are `policy_risk_agent`, the Enterprise Policy & FinOps Risk Specialized SLM for ProcureFlow AI.\n"
        "Follow this exact workflow on every compliance and budget request:\n"
        "1. Procedural SOP Activation: Call `list_skills` and `load_skill` to load `procurement-policy-audit` "
        "and `ap2-hitl-escalation-rules` so you apply exact corporate governance rules.\n"
        "2. Live FinOps & Compliance Audit:\n"
        "   - If `vendor_id` and `order_total_usd` are known, call `audit_vendor_and_order_policy(user_id=..., vendor_id=..., order_total_usd=...)`.\n"
        "   - Always call `get_department_budget_status(user_id=..., proposed_order_usd=...)` when checking department budget.\n"
        "3. Deterministic Classification: Report the exact `governance_route` (`AUTO_INTENT_MANDATE`, "
        "`HITL_CART_MANDATE`, or `HARD_BLOCK`), all `escalation_reasons` / `block_reasons`, vendor tier, "
        "and the exact `budget_gauge_payload` dictionary (`department_id`, `department_name`, "
        "`budget_total_usd`, `current_spent_usd`, `order_amount_usd`, `projected_spent_usd`, "
        "`current_utilization_pct`, `projected_utilization_pct`, `warning_threshold_pct`, `status`)."
    )


def create_policy_agent(model: str | None = None) -> LlmAgent:
    """Instantiate the Policy & FinOps Risk SLM (`LlmAgent`)."""
    settings = get_settings()
    resolved_model = model or settings.slm_model
    callbacks = build_agent_callbacks(
        service_name="policy",
        agent_name="policy_risk_agent",
        default_model=resolved_model,
    )
    skill_toolset = build_lazy_skill_toolset(POLICY_SKILL_NAMES, settings.skills_dir)

    return LlmAgent(
        name="policy_risk_agent",
        model=resolved_model,
        description=(
            "Policy & FinOps Risk SLM (Port 8002): audits department budgets, vendor compliance tiers "
            "(PREFERRED, APPROVED, UNVERIFIED, BLOCKED), anti-split-order spend, and classifies AP2 "
            "governance routes (AUTO_INTENT_MANDATE, HITL_CART_MANDATE, HARD_BLOCK)."
        ),
        instruction=_policy_instruction,
        tools=[
            skill_toolset,
            get_department_budget_status,
            audit_vendor_and_order_policy,
        ],
        before_model_callback=callbacks.before_model_callback,
        after_model_callback=callbacks.after_model_callback,
        before_tool_callback=callbacks.before_tool_callback,
        after_tool_callback=callbacks.after_tool_callback,
    )
