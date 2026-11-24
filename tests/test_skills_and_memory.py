"""Tests for ADK procedural SkillToolset loading, SQLite seeding, episodic memory, and demo reset."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from types import SimpleNamespace
from typing import Any

from google.adk.skills import load_skill_from_dir
from google.adk.tools.skill_toolset import SkillDiscoveryMode, SkillToolset
import pytest

from procureflow_ai.agents.catalog_agent import CATALOG_SKILL_NAMES
from procureflow_ai.agents.orchestrator import ORCHESTRATOR_SKILL_NAMES
from procureflow_ai.agents.policy_agent import POLICY_SKILL_NAMES
from procureflow_ai.agents.settlement_agent import SETTLEMENT_SKILL_NAMES
from procureflow_ai.agents.skill_loader import (
    build_lazy_skill_toolset,
    load_domain_skills,
)
from procureflow_ai.config import get_skills_dir
from procureflow_ai.db.queries import (
    get_active_intent_mandate_for_user,
    list_orders,
    list_personas_with_budgets,
    settle_autonomous_intent_order,
)
from procureflow_ai.db.seed import async_init_and_seed_db
from procureflow_ai.protocols.ap2 import verify_intent_mandate
from procureflow_ai.tools.orchestrator_tools import (
    recall_user_episodes,
    record_user_episode,
)

ALL_EXPECTED_SKILLS = (
    "a2ui-surface-composer",
    "ap2-hitl-escalation-rules",
    "ap2-mandate-lifecycle",
    "hardware-sourcing-sop",
    "procurement-policy-audit",
    "saas-license-sizing",
)


def test_all_six_procedural_skills_load_from_dir() -> None:
    """Verify all 6 SKILL.md playbooks parse cleanly via ADK load_skill_from_dir."""
    skills_dir = get_skills_dir()
    assert skills_dir.exists()

    for skill_name in ALL_EXPECTED_SKILLS:
        skill_path = skills_dir / skill_name
        assert (skill_path / "SKILL.md").is_file()
        skill = load_skill_from_dir(skill_path)
        assert skill.name == skill_name
        assert len(skill.description) >= 20
        assert len(skill.instructions.strip()) >= 100
        assert skill.frontmatter.license == "Apache-2.0"

    loaded_all = load_domain_skills(ALL_EXPECTED_SKILLS, skills_dir=skills_dir)
    assert [s.name for s in loaded_all] == list(ALL_EXPECTED_SKILLS)


@pytest.mark.asyncio
async def test_lazy_skill_toolset_exposes_progressive_disclosure_tools() -> None:
    """Verify SkillToolset uses SkillDiscoveryMode.LAZY and exposes list_skills + load_skill."""
    agent_skill_groups = [
        ORCHESTRATOR_SKILL_NAMES,
        CATALOG_SKILL_NAMES,
        POLICY_SKILL_NAMES,
        SETTLEMENT_SKILL_NAMES,
    ]
    combined = tuple(name for group in agent_skill_groups for name in group)
    assert set(combined) == set(ALL_EXPECTED_SKILLS)

    lazy_toolset = build_lazy_skill_toolset(ALL_EXPECTED_SKILLS)
    assert isinstance(lazy_toolset, SkillToolset)

    lazy_tools = await lazy_toolset.get_tools()
    lazy_tool_names = {t.name for t in lazy_tools}
    # SkillDiscoveryMode.LAZY MUST offer list_skills alongside load_skill
    assert "list_skills" in lazy_tool_names
    assert "load_skill" in lazy_tool_names

    # Contrast with EAGER mode where list_skills is omitted
    eager_toolset = SkillToolset(
        skills=load_domain_skills(ALL_EXPECTED_SKILLS),
        discovery_mode=SkillDiscoveryMode.EAGER,
    )
    eager_tool_names = {t.name for t in await eager_toolset.get_tools()}
    assert "list_skills" not in eager_tool_names
    assert "load_skill" in eager_tool_names


@pytest.mark.asyncio
async def test_sqlite_enterprise_seed_data_and_intent_mandates(
    domain_db_path: Path,
) -> None:
    """Verify seeded vendors, SKUs, personas, department budgets, and Ed25519 IntentMandates."""
    conn = sqlite3.connect(str(domain_db_path))
    try:
        vendor_rows = conn.execute(
            "SELECT vendor_id, tier FROM vendors ORDER BY vendor_id"
        ).fetchall()
        sku_count = conn.execute("SELECT COUNT(*) FROM catalog_skus").fetchone()[0]
        dept_rows = conn.execute(
            "SELECT department_id, budget_total_usd, spent_usd FROM departments"
        ).fetchall()
    finally:
        conn.close()

    assert len(vendor_rows) == 6
    vendor_map = dict(vendor_rows)
    assert vendor_map == {
        "vnd_apple_ent": "PREFERRED",
        "vnd_cloudsoft": "APPROVED",
        "vnd_dell_premier": "APPROVED",
        "vnd_greyimport": "BLOCKED",
        "vnd_quickgear": "UNVERIFIED",
        "vnd_yubico": "PREFERRED",
    }
    assert sku_count >= 10
    assert len(dept_rows) == 3

    personas = await list_personas_with_budgets(db_path=domain_db_path)
    assert [p["user_id"] for p in personas] == ["maya_lin", "alex_rivera", "sam_chen"]

    by_user = {p["user_id"]: p for p in personas}
    assert by_user["maya_lin"]["department_id"] == "dept_ml_rnd"
    assert by_user["maya_lin"]["utilization_pct"] == 26.0
    assert by_user["alex_rivera"]["department_id"] == "dept_it_ops"
    assert by_user["alex_rivera"]["utilization_pct"] == 78.5
    assert by_user["sam_chen"]["department_id"] == "dept_sec"
    assert by_user["sam_chen"]["utilization_pct"] == 20.0

    for uid in ("maya_lin", "alex_rivera", "sam_chen"):
        mandate = await get_active_intent_mandate_for_user(uid, db_path=domain_db_path)
        assert mandate is not None
        assert mandate.max_auto_usd == 500.0
        assert set(mandate.allowed_tiers) == {"PREFERRED", "APPROVED"}
        assert mandate.max_budget_utilization == 0.80
        is_valid, reason = verify_intent_mandate(mandate)
        assert is_valid is True
        assert reason == "VALID"


@pytest.mark.asyncio
async def test_episodic_memory_recall_recording_and_demo_reset(
    domain_db_path: Path,
) -> None:
    """Verify episodic memory recall, new preference recording, and full demo state reset."""
    fake_ctx: Any = SimpleNamespace(state={})
    recalled = await recall_user_episodes(
        user_id="maya_lin",
        query="Apple laptop monitor",
        tool_context=fake_ctx,
    )
    assert recalled["count"] >= 2
    assert any("Apple Silicon" in ep["summary"] for ep in recalled["episodes"])
    assert len(fake_ctx.state["user:last_recalled_episodes"]) >= 2

    recorded = await record_user_episode(
        user_id="sam_chen",
        summary="Prefers Keychron Q1 Pro mechanical keyboards for security workstations.",
        category="preference",
        tags="keyboard,keychron,hardware",
    )
    assert recorded["status"] == "recorded"

    sam_episodes = await recall_user_episodes(user_id="sam_chen", query="Keychron")
    assert sam_episodes["episodes"][0]["summary"].startswith("Prefers Keychron Q1 Pro")

    # Settle an autonomous order to mutate spent_usd and orders table
    await settle_autonomous_intent_order(
        user_id="sam_chen",
        vendor_id="vnd_yubico",
        line_items=[
            {
                "sku_id": "sku_yubikey_5c",
                "name": "YubiKey 5C NFC Enterprise Security Key",
                "quantity": 2,
                "unit_price_usd": 55.0,
                "discount_pct": 0.0,
                "line_total_usd": 110.0,
            }
        ],
        session_id="sess_reset_check",
        db_path=domain_db_path,
    )
    assert len(await list_orders(db_path=domain_db_path)) == 1

    # Reset demo database and assert clean baseline restoration
    await async_init_and_seed_db(domain_db_path, reset=True)
    assert len(await list_orders(db_path=domain_db_path)) == 0

    personas_after = {
        p["user_id"]: p for p in await list_personas_with_budgets(db_path=domain_db_path)
    }
    assert personas_after["sam_chen"]["spent_usd"] == 2400.0
    assert personas_after["alex_rivera"]["spent_usd"] == 7850.0
    assert personas_after["maya_lin"]["spent_usd"] == 6500.0

    sam_after_reset = await recall_user_episodes(user_id="sam_chen")
    assert all("Keychron" not in ep["summary"] for ep in sam_after_reset["episodes"])
