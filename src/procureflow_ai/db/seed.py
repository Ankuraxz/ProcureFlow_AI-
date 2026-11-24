"""Idempotent enterprise seed data for vendors, catalog SKUs, budgets, personas, and AP2 mandates."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3
from typing import Any

from ..config import AP2_AUTO_LIMIT_USD, BUDGET_WARNING_RATIO, get_domain_db_path
from ..protocols.ap2 import (
    create_signed_intent_mandate,
    derive_deterministic_ed25519_keypair,
)
from .schema import SCHEMA_DDL

SEED_VENDORS: list[dict[str, Any]] = [
    {
        "vendor_id": "vnd_apple_ent",
        "name": "Apple Enterprise Direct",
        "tier": "PREFERRED",
        "category": "hardware",
        "sso_ready": 1,
        "default_lead_time_days": 2,
        "notes": "Master enterprise agreement with 2-day priority express depot fulfillment.",
    },
    {
        "vendor_id": "vnd_dell_premier",
        "name": "Dell Premier B2B",
        "tier": "APPROVED",
        "category": "hardware",
        "sso_ready": 1,
        "default_lead_time_days": 3,
        "notes": "Approved global B2B workstation, monitor, and peripheral supplier.",
    },
    {
        "vendor_id": "vnd_yubico",
        "name": "Yubico Direct",
        "tier": "PREFERRED",
        "category": "security",
        "sso_ready": 1,
        "default_lead_time_days": 2,
        "notes": "Preferred hardware security key vendor (FIPS & FIDO2 certified).",
    },
    {
        "vendor_id": "vnd_cloudsoft",
        "name": "CloudSoft SaaS Reseller",
        "tier": "APPROVED",
        "category": "saas",
        "sso_ready": 1,
        "default_lead_time_days": 1,
        "notes": "Approved enterprise SaaS seat reseller with instant SCIM/SSO provisioning.",
    },
    {
        "vendor_id": "vnd_quickgear",
        "name": "QuickGear Marketplace",
        "tier": "UNVERIFIED",
        "category": "accessories",
        "sso_ready": 0,
        "default_lead_time_days": 6,
        "notes": "Third-party marketplace seller pending SOC2/W-9 onboarding; triggers HITL review.",
    },
    {
        "vendor_id": "vnd_greyimport",
        "name": "GreyImport Electronics",
        "tier": "BLOCKED",
        "category": "hardware",
        "sso_ready": 0,
        "default_lead_time_days": 10,
        "notes": "Blocked unauthorized grey-market hardware broker; prohibited by corporate policy.",
    },
]

SEED_CATALOG_SKUS: list[dict[str, Any]] = [
    {
        "sku_id": "sku_yubikey_5c",
        "name": "YubiKey 5C NFC Enterprise Security Key",
        "category": "security_key",
        "vendor_id": "vnd_yubico",
        "unit_price_usd": 55.0,
        "stock_qty": 250,
        "lead_time_days": 2,
        "specs": "USB-C + NFC FIDO2/WebAuthn/PIV hardware security key",
        "recommended_roles": "Security Engineer,IT Ops Manager,ML R&D Lead",
        "billing_cadence": "one_time",
        "sso_supported": 1,
    },
    {
        "sku_id": "sku_logitech_mx3s",
        "name": "Logitech MX Master 3S Wireless Mouse",
        "category": "accessory",
        "vendor_id": "vnd_dell_premier",
        "unit_price_usd": 99.0,
        "stock_qty": 120,
        "lead_time_days": 2,
        "specs": "8K DPI quiet-click ergonomic Bluetooth + Logi Bolt USB-C mouse",
        "recommended_roles": "ML R&D Lead,IT Ops Manager,Security Engineer",
        "billing_cadence": "one_time",
        "sso_supported": 1,
    },
    {
        "sku_id": "sku_keychron_q1",
        "name": "Keychron Q1 Pro Wireless Mechanical Keyboard",
        "category": "accessory",
        "vendor_id": "vnd_dell_premier",
        "unit_price_usd": 199.0,
        "stock_qty": 80,
        "lead_time_days": 3,
        "specs": "CNC aluminum 75% QMK/VIA wireless mechanical keyboard",
        "recommended_roles": "Security Engineer,ML R&D Lead",
        "billing_cadence": "one_time",
        "sso_supported": 1,
    },
    {
        "sku_id": "sku_figma_ent",
        "name": "Figma Organization Seat (Annual)",
        "category": "saas",
        "vendor_id": "vnd_cloudsoft",
        "unit_price_usd": 180.0,
        "stock_qty": 999,
        "lead_time_days": 1,
        "specs": "Annual Figma Organization design/whiteboard seat with SAML SSO & SCIM",
        "recommended_roles": "ML R&D Lead,IT Ops Manager",
        "billing_cadence": "annual",
        "sso_supported": 1,
    },
    {
        "sku_id": "sku_github_copilot",
        "name": "GitHub Copilot Enterprise Seat (Annual)",
        "category": "saas",
        "vendor_id": "vnd_cloudsoft",
        "unit_price_usd": 228.0,
        "stock_qty": 999,
        "lead_time_days": 1,
        "specs": "Annual GitHub Copilot Enterprise AI coding license with SAML SSO & IP indemnity",
        "recommended_roles": "ML R&D Lead,Security Engineer",
        "billing_cadence": "annual",
        "sso_supported": 1,
    },
    {
        "sku_id": "sku_linear_ent",
        "name": "Linear Enterprise Seat (Annual)",
        "category": "saas",
        "vendor_id": "vnd_cloudsoft",
        "unit_price_usd": 144.0,
        "stock_qty": 999,
        "lead_time_days": 1,
        "specs": "Annual Linear Enterprise issue tracking seat with SAML SSO & audit logs",
        "recommended_roles": "ML R&D Lead,IT Ops Manager,Security Engineer",
        "billing_cadence": "annual",
        "sso_supported": 1,
    },
    {
        "sku_id": "sku_mbp14_m4",
        "name": "Apple MacBook Pro 14-inch (M4 Pro, 24GB, 1TB)",
        "category": "laptop",
        "vendor_id": "vnd_apple_ent",
        "unit_price_usd": 2399.0,
        "stock_qty": 35,
        "lead_time_days": 2,
        "specs": "Apple M4 Pro 14-core CPU, 20-core GPU, 24GB Unified Memory, 1TB SSD, 2-day shipping",
        "recommended_roles": "ML R&D Lead,Security Engineer",
        "billing_cadence": "one_time",
        "sso_supported": 1,
    },
    {
        "sku_id": "sku_mbp16_m4max",
        "name": "Apple MacBook Pro 16-inch (M4 Max, 64GB, 2TB)",
        "category": "laptop",
        "vendor_id": "vnd_apple_ent",
        "unit_price_usd": 3899.0,
        "stock_qty": 20,
        "lead_time_days": 3,
        "specs": "Apple M4 Max 16-core CPU, 40-core GPU, 64GB Unified Memory, 2TB SSD, 3-day shipping",
        "recommended_roles": "ML R&D Lead",
        "billing_cadence": "one_time",
        "sso_supported": 1,
    },
    {
        "sku_id": "sku_dell_xps15",
        "name": "Dell Precision 5690 Developer Laptop (32GB, 1TB)",
        "category": "laptop",
        "vendor_id": "vnd_dell_premier",
        "unit_price_usd": 2150.0,
        "stock_qty": 40,
        "lead_time_days": 4,
        "specs": "Intel Core Ultra 9, 32GB LPDDR5x, 1TB NVMe SSD, Ubuntu/Windows Pro, 4-day shipping",
        "recommended_roles": "IT Ops Manager,Security Engineer",
        "billing_cadence": "one_time",
        "sso_supported": 1,
    },
    {
        "sku_id": "sku_dell_u3223qe",
        "name": "Dell UltraSharp 32 4K USB-C Hub Monitor (U3223QE)",
        "category": "monitor",
        "vendor_id": "vnd_dell_premier",
        "unit_price_usd": 899.0,
        "stock_qty": 60,
        "lead_time_days": 3,
        "specs": "31.5-inch 4K UHD IPS Black, 90W USB-C PD + RJ45 Hub, 3-day shipping",
        "recommended_roles": "ML R&D Lead,IT Ops Manager,Security Engineer",
        "billing_cadence": "one_time",
        "sso_supported": 1,
    },
    {
        "sku_id": "sku_apple_studio_display",
        "name": "Apple Studio Display 27-inch 5K USB-C",
        "category": "monitor",
        "vendor_id": "vnd_apple_ent",
        "unit_price_usd": 1599.0,
        "stock_qty": 25,
        "lead_time_days": 2,
        "specs": "27-inch 5K Retina Thunderbolt/USB-C 96W host charging, 2-day shipping",
        "recommended_roles": "ML R&D Lead",
        "billing_cadence": "one_time",
        "sso_supported": 1,
    },
    {
        "sku_id": "sku_gpu_ws_rtx",
        "name": "Precision AI Workstation Tower (Dual RTX 5000 Ada, 128GB)",
        "category": "workstation",
        "vendor_id": "vnd_dell_premier",
        "unit_price_usd": 4200.0,
        "stock_qty": 12,
        "lead_time_days": 5,
        "specs": "Threadripper PRO, Dual NVIDIA RTX 5000 Ada GPUs, 128GB ECC RAM, 4TB NVMe",
        "recommended_roles": "ML R&D Lead",
        "billing_cadence": "one_time",
        "sso_supported": 1,
    },
    {
        "sku_id": "sku_dell_wd22tb4",
        "name": "Dell WD22TB4 Certified Thunderbolt 4 Dock",
        "category": "dock",
        "vendor_id": "vnd_dell_premier",
        "unit_price_usd": 249.0,
        "stock_qty": 90,
        "lead_time_days": 2,
        "specs": "Certified Thunderbolt 4 modular dock with 130W power delivery",
        "recommended_roles": "IT Ops Manager,Security Engineer,ML R&D Lead",
        "billing_cadence": "one_time",
        "sso_supported": 1,
    },
    {
        "sku_id": "sku_quickgear_dock",
        "name": "QuickGear USB-C Dock (12-in-1 Dual 4K)",
        "category": "dock",
        "vendor_id": "vnd_quickgear",
        "unit_price_usd": 149.0,
        "stock_qty": 45,
        "lead_time_days": 6,
        "specs": "12-in-1 USB-C hub from unverified marketplace reseller (6-day shipping)",
        "recommended_roles": "IT Ops Manager",
        "billing_cadence": "one_time",
        "sso_supported": 0,
    },
    {
        "sku_id": "sku_greyimport_gpu",
        "name": "GreyImport Refurb GPU Accelerator (RTX 4090)",
        "category": "workstation",
        "vendor_id": "vnd_greyimport",
        "unit_price_usd": 399.0,
        "stock_qty": 8,
        "lead_time_days": 10,
        "specs": "Unauthorized grey-market refurbished GPU board without manufacturer warranty",
        "recommended_roles": "",
        "billing_cadence": "one_time",
        "sso_supported": 0,
    },
]

SEED_DEPARTMENTS: list[dict[str, Any]] = [
    {
        "department_id": "dept_ml_rnd",
        "name": "ML Research & Dev",
        "budget_total_usd": 25000.0,
        "spent_usd": 6500.0,
        "currency": "USD",
    },
    {
        "department_id": "dept_it_ops",
        "name": "IT Operations",
        "budget_total_usd": 10000.0,
        "spent_usd": 7850.0,
        "currency": "USD",
    },
    {
        "department_id": "dept_sec",
        "name": "Information Security",
        "budget_total_usd": 12000.0,
        "spent_usd": 2400.0,
        "currency": "USD",
    },
]

SEED_PERSONAS: list[dict[str, Any]] = [
    {
        "user_id": "maya_lin",
        "full_name": "Maya Lin",
        "email": "maya.lin@procureflow.enterprise",
        "role": "ML R&D Lead",
        "department_id": "dept_ml_rnd",
        "avatar_initials": "ML",
        "episodic_memories": [
            {
                "episode_id": "epi_maya_001",
                "category": "preference",
                "summary": "Prefers Apple Silicon laptops and USB-C 4K monitors with <= 3-day shipping",
                "tags": "laptop,monitor,apple,usb-c,shipping",
            },
            {
                "episode_id": "epi_maya_002",
                "category": "past_order",
                "summary": "Previously approved Dell UltraSharp U3223QE 32-inch 4K USB-C Hub Monitors for ML lab benches.",
                "tags": "monitor,dell,4k",
            },
        ],
    },
    {
        "user_id": "alex_rivera",
        "full_name": "Alex Rivera",
        "email": "alex.rivera@procureflow.enterprise",
        "role": "IT Ops Manager",
        "department_id": "dept_it_ops",
        "avatar_initials": "AR",
        "episodic_memories": [
            {
                "episode_id": "epi_alex_001",
                "category": "preference",
                "summary": "Manages IT Operations accessories and docks; department Q4 budget is currently near the 80% FinOps warning threshold.",
                "tags": "dock,accessories,budget,it-ops",
            }
        ],
    },
    {
        "user_id": "sam_chen",
        "full_name": "Sam Chen",
        "email": "sam.chen@procureflow.enterprise",
        "role": "Security Engineer",
        "department_id": "dept_sec",
        "avatar_initials": "SC",
        "episodic_memories": [
            {
                "episode_id": "epi_sam_001",
                "category": "preference",
                "summary": "Standardizes on YubiKey 5C NFC hardware keys from Yubico Direct and requires SAML SSO on all SaaS tools.",
                "tags": "security,yubikey,sso,fido2",
            }
        ],
    },
]


def init_and_seed_db(
    db_path: Path | str | None = None,
    *,
    reset: bool = False,
) -> Path:
    """Create schema and idempotently seed enterprise catalog, budgets, personas, and AP2 IntentMandates.

    Args:
        db_path: Optional override path for SQLite database.
        reset: When True, clears dynamic orders, mandates, and resets budgets/memories back to clean demo state.

    Returns:
        Resolved `Path` to the initialized SQLite database.
    """
    target = Path(db_path).expanduser().resolve() if db_path else get_domain_db_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(target), timeout=15.0)
    try:
        conn.executescript(SCHEMA_DDL)

        if reset:
            conn.executescript(
                """
                DELETE FROM orders;
                DELETE FROM ap2_payment_mandates;
                DELETE FROM ap2_cart_mandates;
                DELETE FROM ap2_intent_mandates;
                DELETE FROM session_ui_turns;
                DELETE FROM telemetry_spans;
                DELETE FROM token_usage_ledger;
                DELETE FROM skill_activation_ledger;
                DELETE FROM episodic_memories;
                DELETE FROM personas;
                DELETE FROM departments;
                """
            )

        for v in SEED_VENDORS:
            conn.execute(
                """
                INSERT INTO vendors (vendor_id, name, tier, category, sso_ready, default_lead_time_days, notes)
                VALUES (:vendor_id, :name, :tier, :category, :sso_ready, :default_lead_time_days, :notes)
                ON CONFLICT(vendor_id) DO UPDATE SET
                    name=excluded.name,
                    tier=excluded.tier,
                    category=excluded.category,
                    sso_ready=excluded.sso_ready,
                    default_lead_time_days=excluded.default_lead_time_days,
                    notes=excluded.notes
                """,
                v,
            )

        for sku in SEED_CATALOG_SKUS:
            conn.execute(
                """
                INSERT INTO catalog_skus (
                    sku_id, name, category, vendor_id, unit_price_usd, stock_qty,
                    lead_time_days, specs, recommended_roles, billing_cadence, sso_supported
                ) VALUES (
                    :sku_id, :name, :category, :vendor_id, :unit_price_usd, :stock_qty,
                    :lead_time_days, :specs, :recommended_roles, :billing_cadence, :sso_supported
                )
                ON CONFLICT(sku_id) DO UPDATE SET
                    name=excluded.name,
                    category=excluded.category,
                    vendor_id=excluded.vendor_id,
                    unit_price_usd=excluded.unit_price_usd,
                    stock_qty=excluded.stock_qty,
                    lead_time_days=excluded.lead_time_days,
                    specs=excluded.specs,
                    recommended_roles=excluded.recommended_roles,
                    billing_cadence=excluded.billing_cadence,
                    sso_supported=excluded.sso_supported
                """,
                sku,
            )

        for dept in SEED_DEPARTMENTS:
            conn.execute(
                """
                INSERT INTO departments (department_id, name, budget_total_usd, spent_usd, currency)
                VALUES (:department_id, :name, :budget_total_usd, :spent_usd, :currency)
                ON CONFLICT(department_id) DO NOTHING
                """,
                dept,
            )

        fixed_issued = "2026-01-01T00:00:00+00:00"
        fixed_expires = "2027-12-31T23:59:59+00:00"

        for p in SEED_PERSONAS:
            priv_b64, pub_b64 = derive_deterministic_ed25519_keypair(p["user_id"])
            conn.execute(
                """
                INSERT INTO personas (
                    user_id, full_name, email, role, department_id,
                    public_key_b64, default_private_key_b64, avatar_initials
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO NOTHING
                """,
                (
                    p["user_id"],
                    p["full_name"],
                    p["email"],
                    p["role"],
                    p["department_id"],
                    pub_b64,
                    priv_b64,
                    p["avatar_initials"],
                ),
            )

            for epi in p["episodic_memories"]:
                conn.execute(
                    """
                    INSERT INTO episodic_memories (episode_id, user_id, category, summary, tags, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(episode_id) DO NOTHING
                    """,
                    (
                        epi["episode_id"],
                        p["user_id"],
                        epi["category"],
                        epi["summary"],
                        epi["tags"],
                        fixed_issued,
                    ),
                )

            intent_id = f"im_default_{p['user_id']}"
            intent = create_signed_intent_mandate(
                intent_mandate_id=intent_id,
                user_id=p["user_id"],
                department_id=p["department_id"],
                private_key_b64=priv_b64,
                public_key_b64=pub_b64,
                max_auto_usd=AP2_AUTO_LIMIT_USD,
                allowed_tiers=("PREFERRED", "APPROVED"),
                max_budget_utilization=BUDGET_WARNING_RATIO,
                currency="USD",
                issued_at=fixed_issued,
                expires_at=fixed_expires,
            )
            conn.execute(
                """
                INSERT INTO ap2_intent_mandates (
                    intent_mandate_id, user_id, department_id, max_auto_usd,
                    allowed_tiers_json, max_budget_utilization, currency,
                    canonical_payload, public_key_b64, signature_b64, status,
                    issued_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(intent_mandate_id) DO NOTHING
                """,
                (
                    intent.intent_mandate_id,
                    intent.user_id,
                    intent.department_id,
                    intent.max_auto_usd,
                    json.dumps(intent.allowed_tiers),
                    intent.max_budget_utilization,
                    intent.currency,
                    intent.canonical_payload,
                    intent.public_key_b64,
                    intent.signature_b64,
                    intent.status,
                    intent.issued_at,
                    intent.expires_at,
                ),
            )

        conn.commit()
    finally:
        conn.close()

    return target


async def async_init_and_seed_db(
    db_path: Path | str | None = None,
    *,
    reset: bool = False,
) -> Path:
    """Async wrapper around `init_and_seed_db`."""
    return await asyncio.to_thread(init_and_seed_db, db_path, reset=reset)
