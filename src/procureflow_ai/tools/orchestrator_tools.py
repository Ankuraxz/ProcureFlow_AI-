"""Domain-scoped Concierge Orchestrator custom ADK tools (episodic memory, A2UI surfaces, AP2 HITL pause)."""

from __future__ import annotations

import json
from typing import Any

from google.adk.tools.tool_context import ToolContext

from ..db.queries import (
    get_cart_mandate_record,
    insert_episodic_memory,
    search_episodic_memories,
)
from ..observability.context import push_sse_event
from ..protocols.a2ui import create_a2ui_surface


async def recall_user_episodes(
    user_id: str,
    query: str = "",
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Recall past procurement preferences, historical approvals, and role notes for `user_id`.

    Always call this at the start of a sourcing conversation to personalize SKU selection
    (e.g., preferred laptop architecture, monitor specs, or shipping SLA).

    Args:
        user_id: Employee persona identifier ('maya_lin', 'alex_rivera', 'sam_chen').
        query: Optional keyword filter (e.g., 'laptop', 'monitor', 'shipping').
    """
    episodes = await search_episodic_memories(user_id=user_id, query=query)
    if tool_context is not None:
        tool_context.state["user:last_recalled_episodes"] = [
            e["summary"] for e in episodes[:5]
        ]
    return {
        "user_id": user_id,
        "count": len(episodes),
        "episodes": episodes,
    }


async def record_user_episode(
    user_id: str,
    summary: str,
    category: str = "preference",
    tags: str = "",
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Save a newly expressed user procurement preference or decision outcome into episodic memory.

    Args:
        user_id: Employee persona identifier.
        summary: Concise summary of the preference or decision outcome.
        category: Memory category ('preference', 'past_order', 'policy_note').
        tags: Comma-separated tags (e.g., 'monitor,usb-c,shipping').
    """
    del tool_context
    entry = await insert_episodic_memory(
        user_id=user_id,
        summary=summary,
        category=category,
        tags=tags,
    )
    return {"status": "recorded", "episode": entry}


async def emit_a2ui_surface(
    surface_type: str,
    title: str,
    payload_json: str,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Validate and stream a declarative A2UI widget surface to the React Workspace.

    Supported `surface_type` values:
      - `VendorComparisonCard`
      - `BudgetGaugeCard`
      - `AP2CartMandateApprovalCard`
      - `OrderReceiptCard`

    Args:
        surface_type: One of the four A2UI widget types.
        title: Human-readable card header title.
        payload_json: JSON object string matching the exact schema for `surface_type`.
    """
    try:
        parsed_payload = json.loads(payload_json)
        if not isinstance(parsed_payload, dict):
            return {"error": "payload_json must decode to a JSON object"}
        surface = create_a2ui_surface(
            surface_type=surface_type,  # type: ignore[arg-type]
            title=title,
            payload=parsed_payload,
        )
    except Exception as exc:
        return {
            "error": f"A2UI schema validation failed for '{surface_type}': {exc}",
            "status": "INVALID_PAYLOAD",
        }

    surface_dict = surface.model_dump()
    if tool_context is not None:
        existing = list(tool_context.state.get("temp:emitted_surfaces") or [])
        # Deduplicate identical surface_type + key identifiers within a single turn
        existing.append(surface_dict)
        tool_context.state["temp:emitted_surfaces"] = existing

    push_sse_event({"type": "a2ui_surface", "surface": surface_dict})
    return {
        "status": "emitted",
        "surface_id": surface.surface_id,
        "surface_type": surface.surface_type,
    }


async def request_ap2_human_signature(
    cart_mandate_id: str,
    summary_hint: str = "",
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Pause autonomous execution via ADK confirmation and request an Ed25519 human signature on a staged `CartMandate`.

    Must be called whenever `governance_route == 'HITL_CART_MANDATE'` after staging the `CartMandate`
    on `ap2_settlement_agent`. Also ensures an `AP2CartMandateApprovalCard` A2UI surface is emitted.

    Args:
        cart_mandate_id: Identifier of the staged `PENDING_SIGNATURE` CartMandate.
        summary_hint: Short explanation shown to the reviewer describing why HITL approval is required.
    """
    cart = await get_cart_mandate_record(cart_mandate_id)
    if not cart:
        return {"error": f"CartMandate '{cart_mandate_id}' not found", "status": "FAILED"}

    card_payload = {
        "cart_mandate_id": cart["cart_mandate_id"],
        "user_id": cart["user_id"],
        "department_id": cart["department_id"],
        "vendor_id": cart["vendor_id"],
        "vendor_name": cart["vendor_name"],
        "vendor_tier": cart["vendor_tier"],
        "line_items": cart["line_items"],
        "total_usd": float(cart["total_usd"]),
        "currency": cart["currency"],
        "cart_sha256": cart["cart_sha256"],
        "canonical_payload_to_sign": cart["canonical_payload_to_sign"],
        "escalation_reasons": cart["escalation_reasons"],
        "status": cart["status"],
        "expires_at": cart["expires_at"],
    }

    # Handle resumed confirmation if ADK's confirmation processor re-invoked this tool
    if tool_context is not None and tool_context.tool_confirmation is not None:
        conf = tool_context.tool_confirmation
        return {
            "status": "CONFIRMED" if conf.confirmed else "REJECTED_BY_HUMAN",
            "confirmed": bool(conf.confirmed),
            "cart_mandate_id": cart_mandate_id,
            "confirmation_payload": conf.payload,
        }

    # Ensure AP2CartMandateApprovalCard is emitted if the model hasn't already emitted it
    already_emitted = False
    if tool_context is not None:
        existing = list(tool_context.state.get("temp:emitted_surfaces") or [])
        already_emitted = any(
            s.get("surface_type") == "AP2CartMandateApprovalCard"
            and s.get("payload", {}).get("cart_mandate_id") == cart_mandate_id
            for s in existing
        )
    if not already_emitted:
        surface = create_a2ui_surface(
            surface_type="AP2CartMandateApprovalCard",
            title=f"AP2 CartMandate Review — {cart['vendor_name']} (${float(cart['total_usd']):,.2f})",
            payload=card_payload,
        )
        surface_dict = surface.model_dump()
        if tool_context is not None:
            existing = list(tool_context.state.get("temp:emitted_surfaces") or [])
            existing.append(surface_dict)
            tool_context.state["temp:emitted_surfaces"] = existing
        push_sse_event({"type": "a2ui_surface", "surface": surface_dict})

    hint = (
        summary_hint
        or f"Cryptographic Ed25519 signature required for CartMandate {cart_mandate_id} (${float(cart['total_usd']):,.2f})."
    )

    if tool_context is not None:
        tool_context.state["user:pending_cart_mandate_id"] = cart_mandate_id
        tool_context.state["temp:hitl_cart_mandate"] = card_payload
        if getattr(tool_context, "function_call_id", None):
            tool_context.request_confirmation(hint=hint, payload=card_payload)

    push_sse_event({"type": "hitl_required", "cart_mandate": card_payload})

    return {
        "status": "AWAITING_HUMAN_SIGNATURE",
        "cart_mandate_id": cart_mandate_id,
        "cart_sha256": cart["cart_sha256"],
        "hint": hint,
        "cart_mandate": card_payload,
    }
