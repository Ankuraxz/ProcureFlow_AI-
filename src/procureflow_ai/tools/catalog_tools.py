"""Domain-scoped Catalog & Sourcing custom ADK tools (`catalog_agent` on port 8001)."""

from __future__ import annotations

from typing import Any

from google.adk.tools.tool_context import ToolContext

from ..db.queries import get_sku_by_id, query_catalog_skus


def compute_volume_discount_pct(quantity: int, category: str = "hardware") -> float:
    """Compute deterministic volume discount percentage from SOP brackets.

    Hardware brackets (`hardware-sourcing-sop`):
      - 1-2 units: 0.0%
      - 3-4 units: 5.0%
      - 5+ units: 10.0%

    SaaS brackets (`saas-license-sizing`):
      - 1-2 seats: 0.0%
      - 3-4 seats: 5.0%
      - 5-9 seats: 10.0%
      - 10+ seats: 15.0%
    """
    qty = max(1, int(quantity))
    if category.strip().lower() == "saas":
        if qty >= 10:
            return 15.0
        if qty >= 5:
            return 10.0
        if qty >= 3:
            return 5.0
        return 0.0

    if qty >= 5:
        return 10.0
    if qty >= 3:
        return 5.0
    return 0.0


async def search_catalog_skus(
    query: str,
    category: str = "",
    max_price_usd: float = 0.0,
    include_unverified: bool = True,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Search enterprise hardware and SaaS catalog SKUs with vendor compliance tiers and lead times.

    Args:
        query: Search keywords (e.g., 'MacBook Pro', '4K monitor', 'YubiKey', 'QuickGear dock', 'Figma').
        category: Optional category filter ('laptop', 'workstation', 'monitor', 'security_key', 'accessory', 'dock', 'saas').
        max_price_usd: Optional maximum unit price filter in USD (0.0 means no cap).
        include_unverified: Whether to include UNVERIFIED/BLOCKED marketplace items for comparison/audit.
    """
    del tool_context
    matches = await query_catalog_skus(
        query=query,
        category=category,
        max_price_usd=max_price_usd,
        include_unverified=include_unverified,
    )
    return {
        "query": query,
        "count": len(matches),
        "skus": matches[:8],
    }


async def calculate_bundle_quote(
    sku_id: str,
    quantity: int,
    billing_period: str = "annual",
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Calculate deterministic post-discount bundle pricing for a catalog SKU and quantity.

    Applies hardware volume discount tiers (1-2: 0%, 3-4: 5%, 5+: 10%) or SaaS seat bundle
    sizing & annual/monthly proration rules.

    Args:
        sku_id: Exact catalog SKU identifier (e.g., 'sku_yubikey_5c', 'sku_mbp14_m4').
        quantity: Number of units or seats (>= 1).
        billing_period: 'annual' (default) or 'monthly' (applies 10% flexible-term proration uplift on SaaS).
    """
    del tool_context
    if int(quantity) < 1:
        return {"error": "quantity must be >= 1"}

    sku = await get_sku_by_id(sku_id)
    if not sku:
        return {"error": f"SKU '{sku_id}' not found in catalog"}

    qty = int(quantity)
    cat = str(sku["category"]).lower()
    base_unit_price = float(sku["unit_price_usd"])
    effective_cadence = str(sku["billing_cadence"])

    if cat == "saas" and billing_period.strip().lower() == "monthly":
        base_unit_price = round((base_unit_price / 12.0) * 1.10, 2)
        effective_cadence = "monthly"

    discount_pct = compute_volume_discount_pct(qty, category=cat)
    discounted_unit_price = round(base_unit_price * (1.0 - discount_pct / 100.0), 2)
    subtotal_usd = round(discounted_unit_price * qty, 2)

    return {
        "sku_id": sku["sku_id"],
        "name": sku["name"],
        "category": sku["category"],
        "vendor_id": sku["vendor_id"],
        "vendor_name": sku["vendor_name"],
        "vendor_tier": sku["vendor_tier"],
        "quantity": qty,
        "billing_cadence": effective_cadence,
        "sso_supported": bool(sku["sso_supported"]),
        "list_unit_price_usd": base_unit_price,
        "discount_pct": discount_pct,
        "discounted_unit_price_usd": discounted_unit_price,
        "subtotal_usd": subtotal_usd,
        "currency": "USD",
        "lead_time_days": int(sku["lead_time_days"]),
        "specs": sku["specs"],
        "line_item": {
            "sku_id": sku["sku_id"],
            "name": sku["name"],
            "quantity": qty,
            "unit_price_usd": base_unit_price,
            "discount_pct": discount_pct,
            "line_total_usd": subtotal_usd,
        },
        "vendor_comparison_item": {
            "sku_id": sku["sku_id"],
            "name": sku["name"],
            "vendor_id": sku["vendor_id"],
            "vendor_name": sku["vendor_name"],
            "vendor_tier": sku["vendor_tier"],
            "unit_price_usd": base_unit_price,
            "quantity": qty,
            "discount_pct": discount_pct,
            "subtotal_usd": subtotal_usd,
            "lead_time_days": int(sku["lead_time_days"]),
            "specs": sku["specs"],
            "recommended": sku["vendor_tier"] in ("PREFERRED", "APPROVED"),
        },
    }


async def check_shipping_lead_time(
    sku_ids: list[str],
    destination_region: str = "US-East",
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Check warehouse stock availability and estimated business-day delivery SLA for SKUs.

    Args:
        sku_ids: List of catalog SKU IDs to inspect.
        destination_region: Target shipping region ('US-East', 'US-West', 'EU-Central', 'APAC').
    """
    del tool_context
    region_offset = {
        "us-east": 0,
        "us-west": 0,
        "eu-central": 1,
        "apac": 2,
    }.get(destination_region.strip().lower(), 0)

    estimates: list[dict[str, Any]] = []
    for sid in sku_ids:
        sku = await get_sku_by_id(sid)
        if not sku:
            estimates.append({"sku_id": sid, "error": "SKU not found"})
            continue
        est_days = max(
            1,
            int(sku["lead_time_days"])
            + (0 if sku["category"] == "saas" else region_offset),
        )
        estimates.append(
            {
                "sku_id": sku["sku_id"],
                "name": sku["name"],
                "vendor_name": sku["vendor_name"],
                "vendor_tier": sku["vendor_tier"],
                "in_stock": int(sku["stock_qty"]) > 0,
                "stock_qty": int(sku["stock_qty"]),
                "estimated_delivery_days": est_days,
                "meets_3_day_sla": est_days <= 3,
                "destination_region": destination_region,
            }
        )
    estimates.sort(key=lambda item: item.get("estimated_delivery_days", 99))
    return {
        "destination_region": destination_region,
        "estimates": estimates,
    }
