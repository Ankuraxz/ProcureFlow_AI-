"""Tests for Catalog SKU search, volume discount math, shipping SLAs, and AP2 policy governance routing."""

from __future__ import annotations

from pathlib import Path

import pytest

from procureflow_ai.db.queries import settle_autonomous_intent_order
from procureflow_ai.tools.catalog_tools import (
    calculate_bundle_quote,
    check_shipping_lead_time,
    compute_volume_discount_pct,
    search_catalog_skus,
)
from procureflow_ai.tools.policy_tools import (
    audit_vendor_and_order_policy,
    classify_ap2_governance_route,
    get_department_budget_status,
)


@pytest.mark.parametrize(
    ("quantity", "category", "expected_pct"),
    [
        (1, "hardware", 0.0),
        (2, "hardware", 0.0),
        (3, "hardware", 5.0),
        (4, "hardware", 5.0),
        (5, "hardware", 10.0),
        (12, "hardware", 10.0),
        (2, "saas", 0.0),
        (3, "saas", 5.0),
        (5, "saas", 10.0),
        (10, "saas", 15.0),
    ],
)
def test_volume_discount_brackets(
    quantity: int,
    category: str,
    expected_pct: float,
) -> None:
    """Verify deterministic hardware and SaaS volume discount brackets."""
    assert compute_volume_discount_pct(quantity, category=category) == expected_pct


@pytest.mark.asyncio
async def test_catalog_search_and_bundle_quote_math() -> None:
    """Verify SKU keyword/synonym search, unverified filtering, and bundle discount math."""
    yubi_res = await search_catalog_skus(query="YubiKey security key")
    assert yubi_res["count"] >= 1
    assert yubi_res["skus"][0]["sku_id"] == "sku_yubikey_5c"
    assert yubi_res["skus"][0]["vendor_tier"] == "PREFERRED"

    # Filtering out UNVERIFIED / BLOCKED suppliers
    docks_verified = await search_catalog_skus(
        query="dock",
        category="dock",
        include_unverified=False,
    )
    assert all(
        s["vendor_tier"] in ("PREFERRED", "APPROVED") for s in docks_verified["skus"]
    )
    assert all(s["vendor_id"] != "vnd_quickgear" for s in docks_verified["skus"])

    # 1-2 -> 0%, 3-4 -> 5%, 5+ -> 10%
    q2 = await calculate_bundle_quote(sku_id="sku_yubikey_5c", quantity=2)
    assert q2["discount_pct"] == 0.0
    assert q2["discounted_unit_price_usd"] == 55.0
    assert q2["subtotal_usd"] == 110.0

    q3 = await calculate_bundle_quote(sku_id="sku_yubikey_5c", quantity=3)
    assert q3["discount_pct"] == 5.0
    assert q3["discounted_unit_price_usd"] == 52.25
    assert q3["subtotal_usd"] == 156.75

    q5 = await calculate_bundle_quote(sku_id="sku_yubikey_5c", quantity=5)
    assert q5["discount_pct"] == 10.0
    assert q5["discounted_unit_price_usd"] == 49.50
    assert q5["subtotal_usd"] == 247.50

    # SaaS monthly proration (+10% uplift) + 10-seat 15% discount
    saas_monthly = await calculate_bundle_quote(
        sku_id="sku_figma_ent",
        quantity=10,
        billing_period="monthly",
    )
    assert saas_monthly["billing_cadence"] == "monthly"
    assert saas_monthly["list_unit_price_usd"] == 16.50
    assert saas_monthly["discount_pct"] == 15.0
    assert saas_monthly["discounted_unit_price_usd"] == 14.03
    assert saas_monthly["subtotal_usd"] == 140.30

    invalid_qty = await calculate_bundle_quote(sku_id="sku_yubikey_5c", quantity=0)
    assert "error" in invalid_qty


@pytest.mark.asyncio
async def test_shipping_lead_time_sla_ranking() -> None:
    """Verify shipping lead-time sorting, regional offsets, and 3-day SLA flag."""
    us_east = await check_shipping_lead_time(
        sku_ids=["sku_quickgear_dock", "sku_mbp14_m4", "sku_dell_u3223qe"],
        destination_region="US-East",
    )
    estimates = us_east["estimates"]
    days = [e["estimated_delivery_days"] for e in estimates]
    assert days == sorted(days)
    by_sku = {e["sku_id"]: e for e in estimates}
    assert by_sku["sku_mbp14_m4"]["estimated_delivery_days"] == 2
    assert by_sku["sku_mbp14_m4"]["meets_3_day_sla"] is True
    assert by_sku["sku_dell_u3223qe"]["estimated_delivery_days"] == 3
    assert by_sku["sku_dell_u3223qe"]["meets_3_day_sla"] is True
    assert by_sku["sku_quickgear_dock"]["estimated_delivery_days"] == 6
    assert by_sku["sku_quickgear_dock"]["meets_3_day_sla"] is False

    # APAC adds +2 days to physical hardware, pushing 2-day SKU to 4 days (outside 3-day SLA)
    apac = await check_shipping_lead_time(
        sku_ids=["sku_mbp14_m4", "sku_figma_ent"],
        destination_region="APAC",
    )
    apac_by_sku = {e["sku_id"]: e for e in apac["estimates"]}
    assert apac_by_sku["sku_mbp14_m4"]["estimated_delivery_days"] == 4
    assert apac_by_sku["sku_mbp14_m4"]["meets_3_day_sla"] is False
    # SaaS provisioning ignores physical shipping offset
    assert apac_by_sku["sku_figma_ent"]["estimated_delivery_days"] == 1


@pytest.mark.asyncio
async def test_policy_governance_routing_all_three_paths(
    domain_db_path: Path,
) -> None:
    """Verify AUTO_INTENT_MANDATE, HITL_CART_MANDATE, and HARD_BLOCK policy classifications."""
    # Path 1: AUTO_INTENT_MANDATE (<= $500, PREFERRED/APPROVED, < 80% budget)
    auto_audit = await audit_vendor_and_order_policy(
        user_id="sam_chen",
        vendor_id="vnd_yubico",
        order_total_usd=156.75,
    )
    assert auto_audit["governance_route"] == "AUTO_INTENT_MANDATE"
    assert auto_audit["is_allowed_to_proceed"] is True
    assert auto_audit["requires_human_signature"] is False
    assert auto_audit["reasons"] == []
    assert auto_audit["budget"]["status"] == "HEALTHY"

    # Boundary check: exact $500.00 on APPROVED vendor with healthy budget stays AUTO_INTENT_MANDATE
    route_500, reasons_500 = classify_ap2_governance_route(
        total_usd=500.0,
        vendor_tier="APPROVED",
        projected_utilization_pct=45.0,
    )
    assert route_500 == "AUTO_INTENT_MANDATE"
    assert reasons_500 == []

    # Path 2a: HITL_CART_MANDATE — High value (> $500)
    hitl_high = await audit_vendor_and_order_policy(
        user_id="maya_lin",
        vendor_id="vnd_apple_ent",
        order_total_usd=4798.00,
    )
    assert hitl_high["governance_route"] == "HITL_CART_MANDATE"
    assert hitl_high["requires_human_signature"] is True
    assert any("ORDER_OVER_500_AUTO_LIMIT" in r for r in hitl_high["escalation_reasons"])

    # Path 2b: HITL_CART_MANDATE — UNVERIFIED vendor (vnd_quickgear) even when <= $500
    hitl_unverified = await audit_vendor_and_order_policy(
        user_id="maya_lin",
        vendor_id="vnd_quickgear",
        order_total_usd=298.00,
    )
    assert hitl_unverified["governance_route"] == "HITL_CART_MANDATE"
    assert hitl_unverified["requires_human_signature"] is True
    assert any("UNVERIFIED_VENDOR_TIER" in r for r in hitl_unverified["escalation_reasons"])

    # Path 2c: HITL_CART_MANDATE — >= 80% FinOps budget utilization (alex_rivera at 78.5% + $297 -> 81.47%)
    alex_budget = await get_department_budget_status(
        user_id="alex_rivera",
        proposed_order_usd=297.00,
    )
    assert alex_budget["current_utilization_pct"] == 78.5
    assert alex_budget["projected_utilization_pct"] == 81.47
    assert alex_budget["status"] == "WARNING_OVER_80"

    hitl_budget = await audit_vendor_and_order_policy(
        user_id="alex_rivera",
        vendor_id="vnd_dell_premier",
        order_total_usd=297.00,
    )
    assert hitl_budget["governance_route"] == "HITL_CART_MANDATE"
    assert any(
        "BUDGET_UTILIZATION_GE_80_PCT" in r for r in hitl_budget["escalation_reasons"]
    )

    # Path 2d: HITL_CART_MANDATE — Anti-split-order 24h cumulative spend > $500
    await settle_autonomous_intent_order(
        user_id="sam_chen",
        vendor_id="vnd_dell_premier",
        line_items=[
            {
                "sku_id": "sku_keychron_q1",
                "name": "Keychron Q1 Pro Wireless Mechanical Keyboard",
                "quantity": 2,
                "unit_price_usd": 199.0,
                "discount_pct": 0.0,
                "line_total_usd": 398.0,
            }
        ],
        db_path=domain_db_path,
    )
    split_audit = await audit_vendor_and_order_policy(
        user_id="sam_chen",
        vendor_id="vnd_dell_premier",
        order_total_usd=199.0,
    )
    assert split_audit["governance_route"] == "HITL_CART_MANDATE"
    assert any(
        "ANTI_SPLIT_ORDER_THRESHOLD_EXCEEDED" in r
        for r in split_audit["escalation_reasons"]
    )

    # Path 3a: HARD_BLOCK — BLOCKED vendor (vnd_greyimport)
    block_vendor = await audit_vendor_and_order_policy(
        user_id="maya_lin",
        vendor_id="vnd_greyimport",
        order_total_usd=399.00,
    )
    assert block_vendor["governance_route"] == "HARD_BLOCK"
    assert block_vendor["is_allowed_to_proceed"] is False
    assert any("VENDOR_BLOCKED" in r for r in block_vendor["block_reasons"])

    # Path 3b: HARD_BLOCK — > 100% department budget (alex_rivera has $2,150 remaining)
    block_budget = await audit_vendor_and_order_policy(
        user_id="alex_rivera",
        vendor_id="vnd_dell_premier",
        order_total_usd=4200.00,
    )
    assert block_budget["governance_route"] == "HARD_BLOCK"
    assert block_budget["is_allowed_to_proceed"] is False
    assert block_budget["budget"]["status"] == "EXCEEDED"
    assert any("BUDGET_EXCEEDED" in r for r in block_budget["block_reasons"])
