"""Domain-scoped AP2 Settlement custom ADK tools (`ap2_settlement_agent` on port 8003)."""

from __future__ import annotations

import json
from typing import Any

from google.adk.tools.tool_context import ToolContext

from ..db.queries import (
    create_staged_cart_mandate_record,
    get_active_intent_mandate_for_user,
    mark_cart_mandate_rejected,
    settle_autonomous_intent_order,
    verify_and_settle_cart_mandate,
)
from ..observability.context import current_session_id
from ..protocols.ap2 import AP2VerificationError, verify_intent_mandate


def _parse_json_list(raw: str) -> list[Any]:
    if not raw or not raw.strip():
        return []
    parsed = json.loads(raw)
    if isinstance(parsed, dict):
        return [parsed]
    if not isinstance(parsed, list):
        raise ValueError("Expected a JSON array")
    return parsed


async def check_active_intent_mandate(
    user_id: str,
    order_total_usd: float,
    vendor_tier: str,
    projected_utilization_pct: float,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Verify the user's standing Ed25519 `IntentMandate` signature and check autonomous eligibility.

    Args:
        user_id: Employee persona identifier ('maya_lin', 'alex_rivera', 'sam_chen').
        order_total_usd: Total order amount in USD.
        vendor_tier: Supplier compliance tier ('PREFERRED', 'APPROVED', 'UNVERIFIED', 'BLOCKED').
        projected_utilization_pct: Post-order department budget utilization percentage (0-100+).
    """
    del tool_context
    intent = await get_active_intent_mandate_for_user(user_id)
    if not intent:
        return {
            "eligible_for_auto_settlement": False,
            "reason": f"No active IntentMandate found for user '{user_id}'.",
        }

    sig_valid, sig_reason = verify_intent_mandate(intent)
    tier_upper = vendor_tier.strip().upper()
    within_amount = float(order_total_usd) <= float(intent.max_auto_usd)
    tier_allowed = tier_upper in intent.allowed_tiers
    within_budget = float(projected_utilization_pct) < (
        float(intent.max_budget_utilization) * 100.0
    )

    eligible = sig_valid and within_amount and tier_allowed and within_budget
    reasons: list[str] = []
    if not sig_valid:
        reasons.append(f"IntentMandate signature invalid: {sig_reason}")
    if not within_amount:
        reasons.append(
            f"order_total_usd (${order_total_usd:.2f}) > max_auto_usd (${intent.max_auto_usd:.2f})"
        )
    if not tier_allowed:
        reasons.append(
            f"vendor_tier '{tier_upper}' not in allowed_tiers {intent.allowed_tiers}"
        )
    if not within_budget:
        reasons.append(
            f"projected_utilization_pct ({projected_utilization_pct:.2f}%) >= {intent.max_budget_utilization * 100.0:.0f}%"
        )

    return {
        "eligible_for_auto_settlement": eligible,
        "intent_mandate_id": intent.intent_mandate_id,
        "signature_verified": sig_valid,
        "max_auto_usd": intent.max_auto_usd,
        "allowed_tiers": intent.allowed_tiers,
        "max_budget_utilization_pct": round(intent.max_budget_utilization * 100.0, 2),
        "disqualifying_reasons": reasons,
    }


async def stage_cart_mandate(
    user_id: str,
    vendor_id: str,
    line_items_json: str,
    escalation_reasons_json: str = "[]",
    session_id: str = "",
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Stage a `PENDING_SIGNATURE` AP2 `CartMandate` with canonical JSON and SHA-256 cart digest.

    Call this tool when an order requires HITL human approval (`> $500`, `UNVERIFIED` vendor,
    or `>= 80%` post-order budget utilization). Never stage a `BLOCKED` vendor or `> 100%` budget order.

    Args:
        user_id: Employee persona identifier ('maya_lin', 'alex_rivera', 'sam_chen').
        vendor_id: Vendor identifier (e.g., 'vnd_apple_ent', 'vnd_dell_premier', 'vnd_quickgear').
        line_items_json: JSON array of line items: `[{"sku_id": "...", "name": "...", "quantity": 1, "unit_price_usd": 100.0, "discount_pct": 0.0, "line_total_usd": 100.0}]`.
        escalation_reasons_json: JSON array of policy escalation reason strings.
        session_id: Optional session identifier for audit linkage.
    """
    del tool_context
    try:
        line_items = _parse_json_list(line_items_json)
        escalation_reasons = [str(x) for x in _parse_json_list(escalation_reasons_json)]
    except Exception as exc:
        return {"error": f"Invalid JSON input to stage_cart_mandate: {exc}"}

    eff_session_id = session_id or current_session_id.get() or ""
    try:
        mandate = await create_staged_cart_mandate_record(
            user_id=user_id,
            vendor_id=vendor_id,
            line_items=line_items,
            escalation_reasons=escalation_reasons,
            session_id=eff_session_id,
        )
    except Exception as exc:
        return {"error": str(exc), "status": "FAILED"}

    card_payload = {
        "cart_mandate_id": mandate.cart_mandate_id,
        "user_id": mandate.user_id,
        "department_id": mandate.department_id,
        "vendor_id": mandate.vendor_id,
        "vendor_name": mandate.vendor_name,
        "vendor_tier": mandate.vendor_tier,
        "line_items": [li.model_dump() for li in mandate.line_items],
        "total_usd": mandate.total_usd,
        "currency": mandate.currency,
        "cart_sha256": mandate.cart_sha256,
        "canonical_payload_to_sign": mandate.canonical_payload_to_sign,
        "escalation_reasons": mandate.escalation_reasons,
        "status": mandate.status,
        "expires_at": mandate.expires_at,
    }
    return {
        "status": "PENDING_SIGNATURE",
        "cart_mandate": card_payload,
        "ap2_cart_mandate_approval_card_payload": card_payload,
    }


async def execute_ap2_settlement(
    user_id: str,
    authorization_mode: str,
    vendor_id: str = "",
    line_items_json: str = "[]",
    cart_mandate_id: str = "",
    signature_b64: str = "",
    public_key_b64: str = "",
    session_id: str = "",
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Verify cryptographic AP2 mandate signatures, mint a `PaymentMandate`, and settle the order in SQLite.

    Supports two strict authorization modes:
    1. `AUTO_INTENT_MANDATE`: Verifies the user's standing Ed25519 `IntentMandate` and enforces
       `total_usd <= 500`, `vendor_tier in ('PREFERRED', 'APPROVED')`, and `projected_utilization_pct < 80%`.
       Requires `vendor_id` and `line_items_json`.
    2. `HITL_CART_MANDATE`: Recomputes `cart_sha256` & `canonical_payload_to_sign` from the stored
       `CartMandate` record and verifies the human reviewer's Ed25519 `signature_b64`.
       Requires `cart_mandate_id` and `signature_b64`.

    Args:
        user_id: Employee persona identifier.
        authorization_mode: Either 'AUTO_INTENT_MANDATE' or 'HITL_CART_MANDATE'.
        vendor_id: Required for 'AUTO_INTENT_MANDATE'.
        line_items_json: JSON array of line items (required for 'AUTO_INTENT_MANDATE').
        cart_mandate_id: Staged CartMandate ID (required for 'HITL_CART_MANDATE').
        signature_b64: Base64 raw 64-byte Ed25519 signature over `canonical_payload_to_sign` (required for 'HITL_CART_MANDATE').
        public_key_b64: Optional Base64 raw 32-byte Ed25519 public key (must match registered persona key).
        session_id: Optional session identifier.
    """
    del tool_context
    mode = authorization_mode.strip().upper()
    eff_session_id = session_id or current_session_id.get() or ""

    try:
        if mode == "AUTO_INTENT_MANDATE":
            line_items = _parse_json_list(line_items_json)
            receipt = await settle_autonomous_intent_order(
                user_id=user_id,
                vendor_id=vendor_id,
                line_items=line_items,
                session_id=eff_session_id,
            )
            return {
                "status": "SETTLED",
                "receipt": receipt,
                "order_receipt_card_payload": receipt,
            }

        if mode == "HITL_CART_MANDATE":
            if not cart_mandate_id or not signature_b64:
                return {
                    "error": "HITL_CART_MANDATE requires both cart_mandate_id and signature_b64.",
                    "status": "FAILED",
                }
            receipt = await verify_and_settle_cart_mandate(
                cart_mandate_id=cart_mandate_id,
                user_id=user_id,
                signature_b64=signature_b64,
                public_key_b64=public_key_b64 or None,
                session_id=eff_session_id,
            )
            return {
                "status": "SETTLED",
                "receipt": receipt,
                "order_receipt_card_payload": receipt,
            }

        return {
            "error": f"Unsupported authorization_mode '{authorization_mode}'. Expected AUTO_INTENT_MANDATE or HITL_CART_MANDATE.",
            "status": "FAILED",
        }
    except AP2VerificationError as exc:
        return {"error": str(exc), "status": "VERIFICATION_FAILED"}
    except Exception as exc:
        return {"error": str(exc), "status": "FAILED"}


async def reject_cart_mandate(
    cart_mandate_id: str,
    user_id: str,
    reason: str = "Rejected by human reviewer",
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Cancel and reject a staged `CartMandate` without charging department budget.

    Args:
        cart_mandate_id: Staged CartMandate identifier.
        user_id: Employee persona identifier.
        reason: Human-provided rejection reason.
    """
    del tool_context
    try:
        updated = await mark_cart_mandate_rejected(
            cart_mandate_id=cart_mandate_id,
            user_id=user_id,
            reason=reason,
        )
        return {
            "status": "REJECTED",
            "cart_mandate_id": cart_mandate_id,
            "rejection_reason": updated.get("rejection_reason") or reason,
            "cart_mandate": updated,
        }
    except Exception as exc:
        return {"error": str(exc), "status": "FAILED"}
