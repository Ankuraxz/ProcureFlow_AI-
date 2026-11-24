"""Catalog & Sourcing Specialized SLM Agent (`catalog_agent`, Port 8001, `gemini-3.5-flash-lite`)."""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext

from ..config import get_settings
from ..observability.callbacks import build_agent_callbacks
from ..tools.catalog_tools import (
    calculate_bundle_quote,
    check_shipping_lead_time,
    search_catalog_skus,
)
from .skill_loader import build_lazy_skill_toolset

CATALOG_SKILL_NAMES = ("hardware-sourcing-sop", "saas-license-sizing")


def _catalog_instruction(_ctx: ReadonlyContext) -> str:
    return (
        "You are `catalog_agent`, the Enterprise Catalog & Sourcing Specialized SLM for ProcureFlow AI.\n"
        "Follow this exact workflow on every sourcing request:\n"
        "1. Procedural SOP Activation: Call `list_skills` and then `load_skill` to load the appropriate "
        "procedural playbook (`hardware-sourcing-sop` for laptops, monitors, docks, GPUs, security keys, "
        "and peripherals; `saas-license-sizing` for software subscriptions/seats).\n"
        "2. Grounded SKU Discovery: Call `search_catalog_skus` to find matching SKUs in SQLite. Never invent SKU IDs or prices.\n"
        "3. Deterministic Bundle Math & SLA: Call `calculate_bundle_quote` for the exact requested quantity "
        "(and `check_shipping_lead_time` when delivery SLA matters).\n"
        "4. Structured Output: Return a clear summary including exact `sku_id`, `name`, `vendor_id`, "
        "`vendor_name`, `vendor_tier`, `quantity`, `list_unit_price_usd`, `discount_pct`, `subtotal_usd`, "
        "`lead_time_days`, `specs`, and the exact `line_item` + `vendor_comparison_item` JSON fields so "
        "the Concierge Orchestrator can compose A2UI cards and stage mandates accurately."
    )


def create_catalog_agent(model: str | None = None) -> LlmAgent:
    """Instantiate the Catalog & Sourcing SLM (`LlmAgent`)."""
    settings = get_settings()
    resolved_model = model or settings.slm_model
    callbacks = build_agent_callbacks(
        service_name="catalog",
        agent_name="catalog_agent",
        default_model=resolved_model,
    )
    skill_toolset = build_lazy_skill_toolset(CATALOG_SKILL_NAMES, settings.skills_dir)

    return LlmAgent(
        name="catalog_agent",
        model=resolved_model,
        description=(
            "Catalog & Sourcing SLM (Port 8001): searches enterprise hardware and SaaS SKUs, "
            "applies deterministic volume/seat bundle discount tiers, and evaluates shipping SLAs."
        ),
        instruction=_catalog_instruction,
        tools=[
            skill_toolset,
            search_catalog_skus,
            calculate_bundle_quote,
            check_shipping_lead_time,
        ],
        before_model_callback=callbacks.before_model_callback,
        after_model_callback=callbacks.after_model_callback,
        before_tool_callback=callbacks.before_tool_callback,
        after_tool_callback=callbacks.after_tool_callback,
    )
