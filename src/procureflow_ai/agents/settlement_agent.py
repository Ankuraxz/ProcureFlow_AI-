"""AP2 Cryptographic Settlement Specialized Agent (`ap2_settlement_agent`, Port 8003, `gemini-3.5-flash`)."""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext

from ..config import get_settings
from ..observability.callbacks import build_agent_callbacks
from ..tools.settlement_tools import (
    check_active_intent_mandate,
    execute_ap2_settlement,
    reject_cart_mandate,
    stage_cart_mandate,
)
from .skill_loader import build_lazy_skill_toolset

SETTLEMENT_SKILL_NAMES = ("ap2-mandate-lifecycle",)


def _settlement_instruction(_ctx: ReadonlyContext) -> str:
    return (
        "You are `ap2_settlement_agent`, the Cryptographic Agent Payments Protocol (AP2) Settlement Specialist.\n"
        "Follow these strict security invariants on every call:\n"
        "1. Procedural SOP Activation: Call `list_skills` and `load_skill` to load `ap2-mandate-lifecycle`.\n"
        "2. Autonomous Path (`AUTO_INTENT_MANDATE`):\n"
        "   - First call `check_active_intent_mandate` to verify the user's Ed25519 `IntentMandate` and confirm "
        "`total_usd <= 500.0`, `vendor_tier in ('PREFERRED', 'APPROVED')`, and `projected_utilization_pct < 80.0`.\n"
        "   - Only if `eligible_for_auto_settlement == True`, call `execute_ap2_settlement` with "
        "`authorization_mode='AUTO_INTENT_MANDATE'`, `user_id`, `vendor_id`, and `line_items_json`.\n"
        "3. HITL Staging Path (`HITL_CART_MANDATE` — initial staging):\n"
        "   - Never execute settlement without a human Ed25519 signature when HITL is required!\n"
        "   - Call `stage_cart_mandate(user_id=..., vendor_id=..., line_items_json=..., escalation_reasons_json=...)` "
        "and return the complete `cart_mandate` JSON object (including `cart_mandate_id`, `cart_sha256`, "
        "`canonical_payload_to_sign`, `line_items`, `total_usd`, `escalation_reasons`, `expires_at`).\n"
        "4. HITL Human Decision Path (after human review):\n"
        "   - If decision is APPROVE with `signature_b64`: call `execute_ap2_settlement` with "
        "`authorization_mode='HITL_CART_MANDATE'`, `user_id`, `cart_mandate_id`, `signature_b64`, and `public_key_b64`.\n"
        "   - If decision is REJECT: call `reject_cart_mandate(cart_mandate_id=..., user_id=..., reason=...)`.\n"
        "5. Never settle a `BLOCKED` vendor or an order exceeding 100% department budget. Always return the full "
        "JSON tool payload in your response so the caller has exact mandate IDs, SHA-256 digests, and receipt fields."
    )


def create_settlement_agent(model: str | None = None) -> LlmAgent:
    """Instantiate the AP2 Settlement Specialist (`LlmAgent`)."""
    settings = get_settings()
    resolved_model = model or settings.settlement_model
    callbacks = build_agent_callbacks(
        service_name="settlement",
        agent_name="ap2_settlement_agent",
        default_model=resolved_model,
    )
    skill_toolset = build_lazy_skill_toolset(
        SETTLEMENT_SKILL_NAMES, settings.skills_dir
    )

    return LlmAgent(
        name="ap2_settlement_agent",
        model=resolved_model,
        description=(
            "AP2 Cryptographic Settlement Agent (Port 8003): verifies Ed25519 IntentMandates, "
            "stages SHA-256 CartMandates for HITL signing, verifies WebCrypto/CLI Ed25519 signatures, "
            "and commits atomic PaymentMandate ledger settlements."
        ),
        instruction=_settlement_instruction,
        tools=[
            skill_toolset,
            check_active_intent_mandate,
            stage_cart_mandate,
            execute_ap2_settlement,
            reject_cart_mandate,
        ],
        before_model_callback=callbacks.before_model_callback,
        after_model_callback=callbacks.after_model_callback,
        before_tool_callback=callbacks.before_tool_callback,
        after_tool_callback=callbacks.after_tool_callback,
    )
