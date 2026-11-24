"""Domain-scoped Policy & FinOps Risk custom ADK tools (`policy_risk_agent` on port 8002)."""

from __future__ import annotations

from typing import Any

from google.adk.tools.tool_context import ToolContext

from ..config import get_settings
from ..db.queries import (
    compute_budget_projection,
    get_recent_24h_vendor_spend,
    get_vendor_by_id,
)


def classify_ap2_governance_route(
    *,
    total_usd: float,
    vendor_tier: str,
    projected_utilization_pct: float,
    recent_24h_vendor_spend_usd: float = 0.0,
    auto_limit_usd: float = 500.0,
    warning_threshold_pct: float = 80.0,
) -> tuple[str, list[str]]:
    """Deterministic AP2 HITL decision matrix (`ap2-hitl-escalation-rules`).

    Returns:
        `(route, reasons)` where `route` is one of:
        - `"HARD_BLOCK"`
        - `"HITL_CART_MANDATE"`
        - `"AUTO_INTENT_MANDATE"`
    """
    tier = vendor_tier.strip().upper()
    total = round(float(total_usd), 2)
    proj_pct = round(float(projected_utilization_pct), 2)

    block_reasons: list[str] = []
    if tier == "BLOCKED":
        block_reasons.append(
            "VENDOR_BLOCKED: Supplier is on the corporate prohibited/grey-market blocklist."
        )
    if proj_pct > 100.0:
        block_reasons.append(
            f"BUDGET_EXCEEDED: Projected department spend ({proj_pct:.2f}%) exceeds 100% remaining budget."
        )
    if block_reasons:
        return "HARD_BLOCK", block_reasons

    escalation_reasons: list[str] = []
    if total > auto_limit_usd:
        escalation_reasons.append(
            f"ORDER_OVER_500_AUTO_LIMIT: Order total (${total:.2f}) exceeds autonomous IntentMandate cap (${auto_limit_usd:.2f})."
        )
    if tier == "UNVERIFIED":
        escalation_reasons.append(
            "UNVERIFIED_VENDOR_TIER: Marketplace supplier lacks completed security/tax onboarding; human sign-off mandatory."
        )
    elif tier not in ("PREFERRED", "APPROVED"):
        escalation_reasons.append(
            f"NON_ALLOWLISTED_VENDOR_TIER: Vendor tier '{tier}' is not pre-authorized for autonomous spend."
        )
    if warning_threshold_pct <= proj_pct <= 100.0:
        escalation_reasons.append(
            f"BUDGET_UTILIZATION_GE_80_PCT: Post-order budget utilization ({proj_pct:.2f}%) enters the >= {warning_threshold_pct:.0f}% FinOps warning band."
        )
    if recent_24h_vendor_spend_usd > 0.0 and (recent_24h_vendor_spend_usd + total) > auto_limit_usd and total <= auto_limit_usd:
        escalation_reasons.append(
            f"ANTI_SPLIT_ORDER_THRESHOLD_EXCEEDED: Trailing 24h vendor spend (${recent_24h_vendor_spend_usd:.2f}) + current order (${total:.2f}) exceeds ${auto_limit_usd:.2f}."
        )

    if escalation_reasons:
        return "HITL_CART_MANDATE", escalation_reasons

    return "AUTO_INTENT_MANDATE", []


async def get_department_budget_status(
    user_id: str,
    proposed_order_usd: float = 0.0,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Inspect live department budget, current spend, and post-order utilization percentage.

    Args:
        user_id: Employee persona identifier ('maya_lin', 'alex_rivera', 'sam_chen').
        proposed_order_usd: Proposed order total in USD to project post-order budget utilization.
    """
    del tool_context
    try:
        projection = await compute_budget_projection(
            user_id=user_id,
            proposed_order_usd=proposed_order_usd,
        )
    except KeyError as exc:
        return {"error": str(exc)}

    return {
        **projection,
        "budget_gauge_payload": {
            "department_id": projection["department_id"],
            "department_name": projection["department_name"],
            "budget_total_usd": projection["budget_total_usd"],
            "current_spent_usd": projection["current_spent_usd"],
            "order_amount_usd": projection["order_amount_usd"],
            "projected_spent_usd": projection["projected_spent_usd"],
            "current_utilization_pct": projection["current_utilization_pct"],
            "projected_utilization_pct": projection["projected_utilization_pct"],
            "warning_threshold_pct": projection["warning_threshold_pct"],
            "status": projection["status"],
        },
    }


async def audit_vendor_and_order_policy(
    user_id: str,
    vendor_id: str,
    order_total_usd: float,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Audit vendor compliance tier, anti-split-order spend, and department budget to classify AP2 governance route.

    Returns deterministic classification:
      - `AUTO_INTENT_MANDATE`
      - `HITL_CART_MANDATE`
      - `HARD_BLOCK`

    Args:
        user_id: Employee persona identifier ('maya_lin', 'alex_rivera', 'sam_chen').
        vendor_id: Vendor identifier (e.g., 'vnd_apple_ent', 'vnd_dell_premier', 'vnd_quickgear', 'vnd_greyimport').
        order_total_usd: Post-discount order total in USD.
    """
    del tool_context
    vendor = await get_vendor_by_id(vendor_id)
    if not vendor:
        return {"error": f"Unknown vendor_id '{vendor_id}'"}

    try:
        budget = await compute_budget_projection(
            user_id=user_id,
            proposed_order_usd=order_total_usd,
        )
    except KeyError as exc:
        return {"error": str(exc)}

    recent_24h = await get_recent_24h_vendor_spend(user_id=user_id, vendor_id=vendor_id)
    settings = get_settings()

    route, reasons = classify_ap2_governance_route(
        total_usd=order_total_usd,
        vendor_tier=vendor["tier"],
        projected_utilization_pct=budget["projected_utilization_pct"],
        recent_24h_vendor_spend_usd=recent_24h,
        auto_limit_usd=settings.ap2_auto_limit_usd,
        warning_threshold_pct=round(settings.budget_warning_ratio * 100.0, 2),
    )

    return {
        "governance_route": route,
        "is_allowed_to_proceed": route != "HARD_BLOCK",
        "requires_human_signature": route == "HITL_CART_MANDATE",
        "reasons": reasons,
        "escalation_reasons": reasons if route == "HITL_CART_MANDATE" else [],
        "block_reasons": reasons if route == "HARD_BLOCK" else [],
        "vendor": {
            "vendor_id": vendor["vendor_id"],
            "vendor_name": vendor["name"],
            "vendor_tier": vendor["tier"],
            "sso_ready": bool(vendor["sso_ready"]),
            "notes": vendor["notes"],
        },
        "recent_24h_vendor_spend_usd": recent_24h,
        "budget": budget,
        "budget_gauge_payload": {
            "department_id": budget["department_id"],
            "department_name": budget["department_name"],
            "budget_total_usd": budget["budget_total_usd"],
            "current_spent_usd": budget["current_spent_usd"],
            "order_amount_usd": budget["order_amount_usd"],
            "projected_spent_usd": budget["projected_spent_usd"],
            "current_utilization_pct": budget["current_utilization_pct"],
            "projected_utilization_pct": budget["projected_utilization_pct"],
            "warning_threshold_pct": budget["warning_threshold_pct"],
            "status": budget["status"],
        },
    }
