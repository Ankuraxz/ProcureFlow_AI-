"""Async and sync SQLite queries for catalog, policy, AP2 mandates, orders, and telemetry."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, AsyncIterator
import uuid

import aiosqlite

from ..config import get_domain_db_path, get_settings
from ..protocols.ap2 import (
    AP2VerificationError,
    CartMandate,
    IntentMandate,
    build_cart_mandate,
    canonical_json,
    mint_payment_mandate,
    normalize_line_items,
    sha256_hex,
    verify_intent_mandate,
    verify_stored_cart_mandate,
)
from .seed import init_and_seed_db


def _resolve_db(db_path: Path | str | None = None) -> Path:
    path = Path(db_path).expanduser().resolve() if db_path else get_domain_db_path()
    if not path.exists():
        init_and_seed_db(path)
    return path


@asynccontextmanager
async def connect_domain_db(
    db_path: Path | str | None = None,
) -> AsyncIterator[aiosqlite.Connection]:
    """Open an async SQLite connection configured with WAL, foreign keys, and row factory."""
    resolved = _resolve_db(db_path)
    async with aiosqlite.connect(str(resolved), timeout=15.0) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA busy_timeout=10000;")
        await db.execute("PRAGMA foreign_keys=ON;")
        yield db


# ---------------------------------------------------------------------------
# Personas & Standing IntentMandates
# ---------------------------------------------------------------------------


async def list_personas_with_budgets(
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Return all personas joined with live department budget utilization and active IntentMandate."""
    async with connect_domain_db(db_path) as db:
        cursor = await db.execute(
            """
            SELECT
                p.user_id, p.full_name, p.email, p.role, p.department_id,
                p.public_key_b64, p.avatar_initials,
                d.name AS department_name, d.budget_total_usd, d.spent_usd, d.currency
            FROM personas p
            JOIN departments d ON d.department_id = p.department_id
            ORDER BY CASE p.user_id
                WHEN 'maya_lin' THEN 1
                WHEN 'alex_rivera' THEN 2
                WHEN 'sam_chen' THEN 3
                ELSE 4
            END
            """
        )
        rows = await cursor.fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            total = float(item["budget_total_usd"])
            spent = float(item["spent_usd"])
            util_pct = round((spent / total) * 100.0, 2) if total > 0 else 0.0
            item["remaining_usd"] = round(total - spent, 2)
            item["utilization_pct"] = util_pct
            intent = await get_active_intent_mandate_for_user(
                item["user_id"], db_path=db_path
            )
            item["intent_mandate"] = intent.model_dump() if intent else None
            ep_cur = await db.execute(
                "SELECT episode_id, category, summary, tags, created_at FROM episodic_memories WHERE user_id = ? ORDER BY created_at DESC",
                (item["user_id"],),
            )
            item["episodic_memories"] = [dict(r) for r in await ep_cur.fetchall()]
            results.append(item)
        return results


async def get_persona_context(
    user_id: str,
    db_path: Path | str | None = None,
) -> dict[str, Any] | None:
    """Fetch a single persona with department budget and key metadata."""
    async with connect_domain_db(db_path) as db:
        cur = await db.execute(
            """
            SELECT
                p.user_id, p.full_name, p.email, p.role, p.department_id,
                p.public_key_b64, p.default_private_key_b64, p.avatar_initials,
                d.name AS department_name, d.budget_total_usd, d.spent_usd, d.currency
            FROM personas p
            JOIN departments d ON d.department_id = p.department_id
            WHERE p.user_id = ?
            """,
            (user_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def update_persona_public_key(
    user_id: str,
    public_key_b64: str,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Update the active Ed25519 public key registered by the WebCrypto client."""
    import base64

    try:
        raw = base64.b64decode(public_key_b64, validate=True)
    except Exception as exc:
        raise ValueError("Invalid Base64 public key") from exc
    if len(raw) != 32:
        raise ValueError(f"Ed25519 public key must decode to 32 raw bytes, got {len(raw)}")

    async with connect_domain_db(db_path) as db:
        cur = await db.execute(
            "UPDATE personas SET public_key_b64 = ? WHERE user_id = ?",
            (public_key_b64, user_id),
        )
        await db.commit()
        if cur.rowcount == 0:
            raise KeyError(f"Unknown persona '{user_id}'")
    persona = await get_persona_context(user_id, db_path=db_path)
    assert persona is not None
    return {
        "user_id": user_id,
        "public_key_b64": persona["public_key_b64"],
        "status": "registered",
    }


async def get_active_intent_mandate_for_user(
    user_id: str,
    db_path: Path | str | None = None,
) -> IntentMandate | None:
    """Return the active standing `IntentMandate` for `user_id`."""
    async with connect_domain_db(db_path) as db:
        cur = await db.execute(
            """
            SELECT * FROM ap2_intent_mandates
            WHERE user_id = ? AND status = 'ACTIVE'
            ORDER BY issued_at DESC LIMIT 1
            """,
            (user_id,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        d = dict(row)
        return IntentMandate(
            intent_mandate_id=d["intent_mandate_id"],
            user_id=d["user_id"],
            department_id=d["department_id"],
            max_auto_usd=float(d["max_auto_usd"]),
            allowed_tiers=json.loads(d["allowed_tiers_json"]),
            max_budget_utilization=float(d["max_budget_utilization"]),
            currency=d["currency"],
            canonical_payload=d["canonical_payload"],
            public_key_b64=d["public_key_b64"],
            signature_b64=d["signature_b64"],
            status=d["status"],
            issued_at=d["issued_at"],
            expires_at=d["expires_at"],
        )


# ---------------------------------------------------------------------------
# Catalog & Sourcing Queries
# ---------------------------------------------------------------------------


async def query_catalog_skus(
    query: str = "",
    category: str = "",
    max_price_usd: float = 0.0,
    include_unverified: bool = True,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Search catalog SKUs joined with vendor compliance metadata."""
    async with connect_domain_db(db_path) as db:
        sql = """
            SELECT
                s.sku_id, s.name, s.category, s.vendor_id, s.unit_price_usd,
                s.stock_qty, s.lead_time_days, s.specs, s.recommended_roles,
                s.billing_cadence, s.sso_supported,
                v.name AS vendor_name, v.tier AS vendor_tier, v.notes AS vendor_notes
            FROM catalog_skus s
            JOIN vendors v ON v.vendor_id = s.vendor_id
            WHERE 1=1
        """
        params: list[Any] = []
        if category and category.strip().lower() not in ("all", "any", "*"):
            cat_norm = category.strip().lower()
            sql += " AND (LOWER(s.category) LIKE ? OR LOWER(v.category) LIKE ?)"
            params.extend([f"%{cat_norm}%", f"%{cat_norm}%"])
        if max_price_usd and max_price_usd > 0:
            sql += " AND s.unit_price_usd <= ?"
            params.append(float(max_price_usd))
        if not include_unverified:
            sql += " AND v.tier IN ('PREFERRED', 'APPROVED')"

        cur = await db.execute(sql, params)
        all_rows = [dict(r) for r in await cur.fetchall()]

    if not query or not query.strip():
        return sorted(
            all_rows,
            key=lambda r: (
                _tier_rank(r["vendor_tier"]),
                r["lead_time_days"],
                r["unit_price_usd"],
            ),
        )

    tokens = [
        t
        for t in query.lower().replace("-", " ").replace("/", " ").split()
        if len(t) >= 2 and t not in {"the", "for", "and", "with", "need", "buy", "order", "get"}
    ]
    if not tokens:
        return all_rows

    scored: list[tuple[int, dict[str, Any]]] = []
    for row in all_rows:
        haystack = " ".join(
            [
                str(row["sku_id"]),
                str(row["name"]),
                str(row["category"]),
                str(row["vendor_name"]),
                str(row["vendor_tier"]),
                str(row["specs"]),
                str(row["recommended_roles"]),
            ]
        ).lower()
        haystack_norm = haystack.replace("-", " ")
        hits = sum(1 for tok in tokens if tok in haystack or tok in haystack_norm)
        # Handle common synonyms so natural language prompts always hit deterministic SKUs
        synonyms = {
            "macbook": "apple macbook laptop",
            "laptop": "laptop macbook precision",
            "monitor": "monitor display ultrasharp studio",
            "display": "monitor display",
            "yubikey": "yubikey security_key fido2",
            "security": "security_key yubikey sso",
            "mouse": "logitech mouse accessory",
            "keyboard": "keychron keyboard accessory",
            "dock": "dock thunderbolt usb c hub",
            "gpu": "gpu workstation rtx",
            "workstation": "workstation gpu tower",
            "figma": "figma saas design",
            "copilot": "copilot github saas",
            "linear": "linear saas",
            "quickgear": "quickgear unverified dock",
            "greyimport": "greyimport blocked refurb gpu",
        }
        for tok in tokens:
            if tok in synonyms:
                if any(syn in haystack_norm for syn in synonyms[tok].split()):
                    hits += 1
        if hits > 0:
            scored.append((hits, row))

    if not scored:
        return all_rows[:6]

    scored.sort(
        key=lambda pair: (
            -pair[0],
            _tier_rank(pair[1]["vendor_tier"]),
            pair[1]["lead_time_days"],
            pair[1]["unit_price_usd"],
        )
    )
    return [item for _, item in scored]


def _tier_rank(tier: str) -> int:
    return {"PREFERRED": 0, "APPROVED": 1, "UNVERIFIED": 2, "BLOCKED": 3}.get(tier, 4)


async def get_sku_by_id(
    sku_id: str,
    db_path: Path | str | None = None,
) -> dict[str, Any] | None:
    """Fetch a single catalog SKU joined with vendor details."""
    async with connect_domain_db(db_path) as db:
        cur = await db.execute(
            """
            SELECT
                s.*, v.name AS vendor_name, v.tier AS vendor_tier, v.notes AS vendor_notes
            FROM catalog_skus s
            JOIN vendors v ON v.vendor_id = s.vendor_id
            WHERE s.sku_id = ?
            """,
            (sku_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_vendor_by_id(
    vendor_id: str,
    db_path: Path | str | None = None,
) -> dict[str, Any] | None:
    """Fetch a single vendor record."""
    async with connect_domain_db(db_path) as db:
        cur = await db.execute(
            "SELECT * FROM vendors WHERE vendor_id = ?",
            (vendor_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Policy, Budget & Anti-Split-Order Queries
# ---------------------------------------------------------------------------


async def compute_budget_projection(
    user_id: str,
    proposed_order_usd: float = 0.0,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Compute live and projected department budget utilization metrics."""
    persona = await get_persona_context(user_id, db_path=db_path)
    if not persona:
        raise KeyError(f"Unknown user_id '{user_id}'")

    settings = get_settings()
    warning_pct = round(settings.budget_warning_ratio * 100.0, 2)
    budget_total = round(float(persona["budget_total_usd"]), 2)
    current_spent = round(float(persona["spent_usd"]), 2)
    order_amt = round(max(0.0, float(proposed_order_usd)), 2)
    projected_spent = round(current_spent + order_amt, 2)

    current_util = round((current_spent / budget_total) * 100.0, 2) if budget_total > 0 else 0.0
    projected_util = (
        round((projected_spent / budget_total) * 100.0, 2) if budget_total > 0 else 0.0
    )

    if projected_util > 100.0:
        status = "EXCEEDED"
    elif projected_util >= warning_pct:
        status = "WARNING_OVER_80"
    else:
        status = "HEALTHY"

    return {
        "user_id": user_id,
        "department_id": persona["department_id"],
        "department_name": persona["department_name"],
        "budget_total_usd": budget_total,
        "current_spent_usd": current_spent,
        "remaining_before_order_usd": round(budget_total - current_spent, 2),
        "order_amount_usd": order_amt,
        "projected_spent_usd": projected_spent,
        "projected_remaining_usd": round(budget_total - projected_spent, 2),
        "current_utilization_pct": current_util,
        "projected_utilization_pct": projected_util,
        "warning_threshold_pct": warning_pct,
        "status": status,
    }


async def get_recent_24h_vendor_spend(
    user_id: str,
    vendor_id: str,
    db_path: Path | str | None = None,
) -> float:
    """Sum settled orders for `user_id` with `vendor_id` over the trailing 24 hours."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    async with connect_domain_db(db_path) as db:
        cur = await db.execute(
            """
            SELECT COALESCE(SUM(total_usd), 0.0) AS recent_total
            FROM orders
            WHERE user_id = ? AND vendor_id = ? AND status = 'SETTLED' AND created_at >= ?
            """,
            (user_id, vendor_id, cutoff),
        )
        row = await cur.fetchone()
        return round(float(row["recent_total"]) if row else 0.0, 2)


# ---------------------------------------------------------------------------
# Episodic Memory Queries
# ---------------------------------------------------------------------------


async def search_episodic_memories(
    user_id: str,
    query: str = "",
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Retrieve episodic memories for `user_id`, ranked by keyword match and recency."""
    async with connect_domain_db(db_path) as db:
        cur = await db.execute(
            """
            SELECT episode_id, user_id, category, summary, tags, created_at
            FROM episodic_memories
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (user_id,),
        )
        rows = [dict(r) for r in await cur.fetchall()]

    if not query or not query.strip():
        return rows

    tokens = [t for t in query.lower().split() if len(t) >= 2]
    if not tokens:
        return rows

    ranked = sorted(
        rows,
        key=lambda r: (
            -sum(1 for t in tokens if t in f"{r['summary']} {r['tags']}".lower()),
            r["created_at"],
        ),
    )
    return ranked


async def insert_episodic_memory(
    user_id: str,
    summary: str,
    category: str = "preference",
    tags: str = "",
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Persist a new episodic memory entry for `user_id`."""
    episode_id = f"epi_{uuid.uuid4().hex[:10]}"
    now = datetime.now(timezone.utc).isoformat()
    async with connect_domain_db(db_path) as db:
        await db.execute(
            """
            INSERT INTO episodic_memories (episode_id, user_id, category, summary, tags, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (episode_id, user_id, category, summary, tags, now),
        )
        await db.commit()
    return {
        "episode_id": episode_id,
        "user_id": user_id,
        "category": category,
        "summary": summary,
        "tags": tags,
        "created_at": now,
    }


# ---------------------------------------------------------------------------
# AP2 Mandate Staging, Verification & Atomic Ledger Settlement
# ---------------------------------------------------------------------------


async def create_staged_cart_mandate_record(
    *,
    user_id: str,
    vendor_id: str,
    line_items: Sequence[dict[str, Any]],
    escalation_reasons: Sequence[str] = (),
    session_id: str = "",
    db_path: Path | str | None = None,
) -> CartMandate:
    """Validate vendor/user, compute deterministic SHA-256 cart digest, and persist `PENDING_SIGNATURE` CartMandate."""
    persona = await get_persona_context(user_id, db_path=db_path)
    if not persona:
        raise ValueError(f"Unknown user_id '{user_id}'")
    vendor = await get_vendor_by_id(vendor_id, db_path=db_path)
    if not vendor:
        raise ValueError(f"Unknown vendor_id '{vendor_id}'")
    if vendor["tier"] == "BLOCKED":
        raise AP2VerificationError(
            f"Vendor '{vendor['name']}' is BLOCKED by corporate policy; cannot stage CartMandate."
        )

    mandate = build_cart_mandate(
        user_id=user_id,
        department_id=persona["department_id"],
        vendor_id=vendor_id,
        vendor_name=vendor["name"],
        vendor_tier=vendor["tier"],
        line_items=line_items,
        escalation_reasons=escalation_reasons,
        session_id=session_id,
    )

    budget = await compute_budget_projection(
        user_id, mandate.total_usd, db_path=db_path
    )
    if budget["projected_utilization_pct"] > 100.0:
        raise AP2VerificationError(
            f"Order total ${mandate.total_usd:.2f} exceeds remaining department budget (${budget['remaining_before_order_usd']:.2f})."
        )

    async with connect_domain_db(db_path) as db:
        await db.execute(
            """
            INSERT INTO ap2_cart_mandates (
                cart_mandate_id, session_id, user_id, department_id, vendor_id,
                vendor_name, vendor_tier, line_items_json, total_usd, currency,
                cart_sha256, canonical_payload_to_sign, escalation_reasons_json,
                status, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mandate.cart_mandate_id,
                mandate.session_id,
                mandate.user_id,
                mandate.department_id,
                mandate.vendor_id,
                mandate.vendor_name,
                mandate.vendor_tier,
                json.dumps([li.model_dump() for li in mandate.line_items]),
                mandate.total_usd,
                mandate.currency,
                mandate.cart_sha256,
                mandate.canonical_payload_to_sign,
                json.dumps(mandate.escalation_reasons),
                mandate.status,
                mandate.created_at,
                mandate.expires_at,
            ),
        )
        await db.commit()
    return mandate


async def get_cart_mandate_record(
    cart_mandate_id: str,
    db_path: Path | str | None = None,
) -> dict[str, Any] | None:
    """Load a staged or decided `CartMandate` record from SQLite."""
    async with connect_domain_db(db_path) as db:
        cur = await db.execute(
            "SELECT * FROM ap2_cart_mandates WHERE cart_mandate_id = ?",
            (cart_mandate_id,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d["line_items"] = json.loads(d.pop("line_items_json"))
        d["escalation_reasons"] = json.loads(d.pop("escalation_reasons_json"))
        return d


async def settle_autonomous_intent_order(
    *,
    user_id: str,
    vendor_id: str,
    line_items: Sequence[dict[str, Any]],
    session_id: str = "",
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Verify standing `IntentMandate` + all policy guardrails and atomically settle an autonomous order."""
    persona = await get_persona_context(user_id, db_path=db_path)
    if not persona:
        raise AP2VerificationError(f"Unknown user_id '{user_id}'")
    vendor = await get_vendor_by_id(vendor_id, db_path=db_path)
    if not vendor:
        raise AP2VerificationError(f"Unknown vendor_id '{vendor_id}'")

    intent = await get_active_intent_mandate_for_user(user_id, db_path=db_path)
    if not intent:
        raise AP2VerificationError(f"No active IntentMandate found for '{user_id}'")

    valid_intent, reason = verify_intent_mandate(intent)
    if not valid_intent:
        raise AP2VerificationError(f"IntentMandate verification failed: {reason}")

    normalized_items = normalize_line_items(line_items)
    if not normalized_items:
        raise AP2VerificationError("Cannot settle an empty line_items list")
    total_usd = round(sum(item["line_total_usd"] for item in normalized_items), 2)

    settings = get_settings()
    auto_cap = min(float(intent.max_auto_usd), float(settings.ap2_auto_limit_usd))
    if total_usd > auto_cap:
        raise AP2VerificationError(
            f"Order total ${total_usd:.2f} exceeds autonomous IntentMandate limit ${auto_cap:.2f}; HITL CartMandate required."
        )

    if vendor["tier"] not in intent.allowed_tiers or vendor["tier"] not in (
        "PREFERRED",
        "APPROVED",
    ):
        raise AP2VerificationError(
            f"Vendor tier '{vendor['tier']}' is not eligible for autonomous IntentMandate settlement."
        )

    budget = await compute_budget_projection(user_id, total_usd, db_path=db_path)
    max_util_pct = round(
        min(float(intent.max_budget_utilization), float(settings.budget_warning_ratio))
        * 100.0,
        2,
    )
    if budget["projected_utilization_pct"] >= max_util_pct:
        raise AP2VerificationError(
            f"Projected budget utilization {budget['projected_utilization_pct']:.2f}% >= {max_util_pct:.2f}% warning band; HITL CartMandate required."
        )

    recent_24h = await get_recent_24h_vendor_spend(user_id, vendor_id, db_path=db_path)
    if recent_24h > 0 and (recent_24h + total_usd) > auto_cap:
        raise AP2VerificationError(
            f"Anti-split-order rule triggered: trailing 24h spend (${recent_24h:.2f}) + current (${total_usd:.2f}) > ${auto_cap:.2f}."
        )

    order_id = f"ord_{uuid.uuid4().hex[:10]}"
    cart_digest = sha256_hex(
        canonical_json(
            {
                "order_id": order_id,
                "user_id": user_id,
                "department_id": persona["department_id"],
                "vendor_id": vendor_id,
                "currency": "USD",
                "total_usd": total_usd,
                "line_items": normalized_items,
            }
        )
    )
    payment_mandate = mint_payment_mandate(
        order_id=order_id,
        authorization_mode="AUTO_INTENT_MANDATE",
        intent_mandate_id=intent.intent_mandate_id,
        cart_mandate_id=None,
        user_id=user_id,
        department_id=persona["department_id"],
        vendor_id=vendor_id,
        total_usd=total_usd,
        cart_sha256=cart_digest,
        signer_public_key_b64=intent.public_key_b64,
        source_signature_b64=intent.signature_b64,
    )

    lead_days = await _estimate_lead_time_days(normalized_items, vendor["default_lead_time_days"], db_path=db_path)
    sig_preview = f"{intent.signature_b64[:16]}...{intent.signature_b64[-8:]}"

    async with connect_domain_db(db_path) as db:
        await db.execute("BEGIN IMMEDIATE;")
        try:
            await db.execute(
                "UPDATE departments SET spent_usd = ROUND(spent_usd + ?, 2) WHERE department_id = ?",
                (total_usd, persona["department_id"]),
            )
            await db.execute(
                """
                INSERT INTO ap2_payment_mandates (
                    payment_mandate_id, order_id, authorization_mode, intent_mandate_id,
                    cart_mandate_id, user_id, department_id, vendor_id, total_usd,
                    currency, cart_sha256, settlement_digest_sha256, signer_public_key_b64,
                    source_signature_b64, settlement_signature_b64, status, settled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payment_mandate.payment_mandate_id,
                    order_id,
                    payment_mandate.authorization_mode,
                    payment_mandate.intent_mandate_id,
                    None,
                    user_id,
                    persona["department_id"],
                    vendor_id,
                    total_usd,
                    "USD",
                    cart_digest,
                    payment_mandate.settlement_digest_sha256,
                    payment_mandate.signer_public_key_b64,
                    payment_mandate.source_signature_b64,
                    payment_mandate.settlement_signature_b64,
                    "SETTLED",
                    payment_mandate.settled_at,
                ),
            )
            await db.execute(
                """
                INSERT INTO orders (
                    order_id, session_id, user_id, department_id, vendor_id, vendor_name,
                    authorization_mode, intent_mandate_id, cart_mandate_id, payment_mandate_id,
                    line_items_json, total_usd, currency, cart_sha256, signature_preview,
                    estimated_delivery_days, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    session_id,
                    user_id,
                    persona["department_id"],
                    vendor_id,
                    vendor["name"],
                    "AUTO_INTENT_MANDATE",
                    intent.intent_mandate_id,
                    None,
                    payment_mandate.payment_mandate_id,
                    json.dumps(normalized_items),
                    total_usd,
                    "USD",
                    cart_digest,
                    sig_preview,
                    lead_days,
                    "SETTLED",
                    payment_mandate.settled_at,
                ),
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    return {
        "order_id": order_id,
        "status": "SETTLED",
        "authorization_mode": "AUTO_INTENT_MANDATE",
        "vendor_name": vendor["name"],
        "department_name": persona["department_name"],
        "total_usd": total_usd,
        "line_items": normalized_items,
        "intent_mandate_id": intent.intent_mandate_id,
        "cart_mandate_id": None,
        "payment_mandate_id": payment_mandate.payment_mandate_id,
        "cart_sha256": cart_digest,
        "signature_preview": sig_preview,
        "estimated_delivery_days": lead_days,
        "created_at": payment_mandate.settled_at,
    }


async def list_session_cart_mandates(
    session_id: str,
    status: str | None = None,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Return CartMandate records created for `session_id` (optionally filtered by `status`)."""
    if not session_id:
        return []
    async with connect_domain_db(db_path) as db:
        sql = "SELECT * FROM ap2_cart_mandates WHERE session_id = ?"
        params: list[Any] = [session_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at ASC"
        cur = await db.execute(sql, params)
        rows = await cur.fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            d["line_items"] = json.loads(d.pop("line_items_json"))
            d["escalation_reasons"] = json.loads(d.pop("escalation_reasons_json"))
            out.append(d)
        return out


async def verify_and_settle_cart_mandate(
    *,
    cart_mandate_id: str,
    user_id: str,
    signature_b64: str,
    public_key_b64: str | None = None,
    session_id: str = "",
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Recompute digest from stored DB fields, verify Ed25519 signature, and atomically settle order."""
    cart = await get_cart_mandate_record(cart_mandate_id, db_path=db_path)
    if not cart:
        raise AP2VerificationError(f"CartMandate '{cart_mandate_id}' not found")
    if cart["user_id"] != user_id:
        raise AP2VerificationError(
            f"CartMandate '{cart_mandate_id}' belongs to '{cart['user_id']}', not '{user_id}'"
        )

    persona = await get_persona_context(user_id, db_path=db_path)
    if not persona:
        raise AP2VerificationError(f"Unknown persona '{user_id}'")

    registered_pub_key = persona["public_key_b64"]
    effective_pub_key = public_key_b64 or registered_pub_key
    if effective_pub_key != registered_pub_key:
        raise AP2VerificationError(
            "UNTRUSTED_SIGNER_PUBLIC_KEY: Provided public_key_b64 does not match the persona's registered Ed25519 key."
        )

    is_valid, reason_code, recomputed_sha256 = verify_stored_cart_mandate(
        cart_mandate_id=cart["cart_mandate_id"],
        user_id=cart["user_id"],
        department_id=cart["department_id"],
        vendor_id=cart["vendor_id"],
        currency=cart["currency"],
        total_usd=float(cart["total_usd"]),
        line_items=cart["line_items"],
        stored_cart_sha256=cart["cart_sha256"],
        stored_canonical_payload=cart["canonical_payload_to_sign"],
        signature_b64=signature_b64,
        public_key_b64=effective_pub_key,
    )
    if not is_valid:
        raise AP2VerificationError(
            f"AP2 cryptographic verification failed ({reason_code}) for CartMandate '{cart_mandate_id}'."
        )

    if cart["status"] == "SIGNED_SETTLED":
        # Idempotent return ONLY after valid cryptographic Ed25519 verification
        existing_orders = await list_orders(user_id=user_id, db_path=db_path)
        for o in existing_orders:
            if o.get("cart_mandate_id") == cart_mandate_id:
                return o
        raise AP2VerificationError(f"CartMandate '{cart_mandate_id}' is already settled")
    if cart["status"] != "PENDING_SIGNATURE":
        raise AP2VerificationError(
            f"CartMandate '{cart_mandate_id}' cannot be settled from status '{cart['status']}'"
        )

    vendor = await get_vendor_by_id(cart["vendor_id"], db_path=db_path)
    if not vendor or vendor["tier"] == "BLOCKED":
        raise AP2VerificationError("Vendor is BLOCKED; settlement refused.")

    total_usd = round(float(cart["total_usd"]), 2)
    budget = await compute_budget_projection(user_id, total_usd, db_path=db_path)
    if budget["projected_utilization_pct"] > 100.0:
        raise AP2VerificationError(
            f"Insufficient department budget for ${total_usd:.2f} settlement."
        )

    order_id = f"ord_{uuid.uuid4().hex[:10]}"
    payment_mandate = mint_payment_mandate(
        order_id=order_id,
        authorization_mode="HITL_CART_MANDATE",
        intent_mandate_id=None,
        cart_mandate_id=cart_mandate_id,
        user_id=user_id,
        department_id=cart["department_id"],
        vendor_id=cart["vendor_id"],
        total_usd=total_usd,
        cart_sha256=recomputed_sha256,
        signer_public_key_b64=effective_pub_key,
        source_signature_b64=signature_b64,
    )

    normalized_items = normalize_line_items(cart["line_items"])
    lead_days = await _estimate_lead_time_days(
        normalized_items, vendor["default_lead_time_days"], db_path=db_path
    )
    sig_preview = f"{signature_b64[:16]}...{signature_b64[-8:]}"
    eff_session_id = session_id or cart.get("session_id") or ""

    async with connect_domain_db(db_path) as db:
        await db.execute("BEGIN IMMEDIATE;")
        try:
            await db.execute(
                """
                UPDATE ap2_cart_mandates
                SET status = 'SIGNED_SETTLED', public_key_b64 = ?, signature_b64 = ?, decided_at = ?
                WHERE cart_mandate_id = ? AND status = 'PENDING_SIGNATURE'
                """,
                (
                    effective_pub_key,
                    signature_b64,
                    payment_mandate.settled_at,
                    cart_mandate_id,
                ),
            )
            await db.execute(
                "UPDATE departments SET spent_usd = ROUND(spent_usd + ?, 2) WHERE department_id = ?",
                (total_usd, cart["department_id"]),
            )
            await db.execute(
                """
                INSERT INTO ap2_payment_mandates (
                    payment_mandate_id, order_id, authorization_mode, intent_mandate_id,
                    cart_mandate_id, user_id, department_id, vendor_id, total_usd,
                    currency, cart_sha256, settlement_digest_sha256, signer_public_key_b64,
                    source_signature_b64, settlement_signature_b64, status, settled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payment_mandate.payment_mandate_id,
                    order_id,
                    "HITL_CART_MANDATE",
                    None,
                    cart_mandate_id,
                    user_id,
                    cart["department_id"],
                    cart["vendor_id"],
                    total_usd,
                    cart["currency"],
                    recomputed_sha256,
                    payment_mandate.settlement_digest_sha256,
                    effective_pub_key,
                    signature_b64,
                    payment_mandate.settlement_signature_b64,
                    "SETTLED",
                    payment_mandate.settled_at,
                ),
            )
            await db.execute(
                """
                INSERT INTO orders (
                    order_id, session_id, user_id, department_id, vendor_id, vendor_name,
                    authorization_mode, intent_mandate_id, cart_mandate_id, payment_mandate_id,
                    line_items_json, total_usd, currency, cart_sha256, signature_preview,
                    estimated_delivery_days, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    eff_session_id,
                    user_id,
                    cart["department_id"],
                    cart["vendor_id"],
                    cart["vendor_name"],
                    "HITL_CART_MANDATE",
                    None,
                    cart_mandate_id,
                    payment_mandate.payment_mandate_id,
                    json.dumps(normalized_items),
                    total_usd,
                    cart["currency"],
                    recomputed_sha256,
                    sig_preview,
                    lead_days,
                    "SETTLED",
                    payment_mandate.settled_at,
                ),
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    return {
        "order_id": order_id,
        "status": "SETTLED",
        "authorization_mode": "HITL_CART_MANDATE",
        "vendor_name": cart["vendor_name"],
        "department_name": persona["department_name"],
        "total_usd": total_usd,
        "line_items": normalized_items,
        "intent_mandate_id": None,
        "cart_mandate_id": cart_mandate_id,
        "payment_mandate_id": payment_mandate.payment_mandate_id,
        "cart_sha256": recomputed_sha256,
        "signature_preview": sig_preview,
        "estimated_delivery_days": lead_days,
        "created_at": payment_mandate.settled_at,
    }


async def mark_cart_mandate_rejected(
    *,
    cart_mandate_id: str,
    user_id: str,
    reason: str = "Rejected by human reviewer",
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Mark a staged `CartMandate` as `REJECTED` without deducting budget."""
    cart = await get_cart_mandate_record(cart_mandate_id, db_path=db_path)
    if not cart:
        raise AP2VerificationError(f"CartMandate '{cart_mandate_id}' not found")
    if cart["user_id"] != user_id:
        raise AP2VerificationError(
            f"CartMandate '{cart_mandate_id}' belongs to '{cart['user_id']}', not '{user_id}'"
        )
    now = datetime.now(timezone.utc).isoformat()
    async with connect_domain_db(db_path) as db:
        await db.execute(
            """
            UPDATE ap2_cart_mandates
            SET status = 'REJECTED', rejection_reason = ?, decided_at = ?
            WHERE cart_mandate_id = ?
            """,
            (reason, now, cart_mandate_id),
        )
        await db.commit()
    updated = await get_cart_mandate_record(cart_mandate_id, db_path=db_path)
    assert updated is not None
    return updated


async def _estimate_lead_time_days(
    line_items: Sequence[dict[str, Any]],
    default_days: int,
    db_path: Path | str | None = None,
) -> int:
    max_days = default_days
    for item in line_items:
        sku = await get_sku_by_id(item["sku_id"], db_path=db_path)
        if sku and int(sku["lead_time_days"]) > max_days:
            max_days = int(sku["lead_time_days"])
    return max_days


async def list_orders(
    user_id: str | None = None,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Return recent settled orders joined with their AP2 PaymentMandate audit chain."""
    async with connect_domain_db(db_path) as db:
        sql = """
            SELECT
                o.*,
                d.name AS department_name,
                pm.settlement_digest_sha256,
                pm.signer_public_key_b64,
                pm.source_signature_b64,
                pm.settlement_signature_b64,
                pm.settled_at
            FROM orders o
            JOIN departments d ON d.department_id = o.department_id
            JOIN ap2_payment_mandates pm ON pm.payment_mandate_id = o.payment_mandate_id
        """
        params: list[Any] = []
        if user_id:
            sql += " WHERE o.user_id = ?"
            params.append(user_id)
        sql += " ORDER BY o.created_at DESC"
        cur = await db.execute(sql, params)
        rows = await cur.fetchall()
        results: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["line_items"] = json.loads(d.pop("line_items_json"))
            results.append(d)
        return results


# ---------------------------------------------------------------------------
# Session UI Turn Persistence (Chat + Rich A2UI Surfaces)
# ---------------------------------------------------------------------------


async def append_session_ui_turn(
    *,
    session_id: str,
    user_id: str,
    role: str,
    content: str,
    surfaces: Sequence[dict[str, Any]] = (),
    hitl_mandate: dict[str, Any] | None = None,
    trace_id: str = "",
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Persist a user or assistant turn with associated A2UI surfaces for clean UI reload."""
    turn_id = f"trn_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    async with connect_domain_db(db_path) as db:
        await db.execute(
            """
            INSERT INTO session_ui_turns (
                turn_id, session_id, user_id, role, content,
                surfaces_json, hitl_mandate_json, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                turn_id,
                session_id,
                user_id,
                role,
                content,
                json.dumps(list(surfaces)),
                json.dumps(hitl_mandate) if hitl_mandate else None,
                trace_id,
                now,
            ),
        )
        await db.commit()
    return {
        "turn_id": turn_id,
        "session_id": session_id,
        "user_id": user_id,
        "role": role,
        "content": content,
        "surfaces": list(surfaces),
        "hitl_mandate": hitl_mandate,
        "trace_id": trace_id,
        "created_at": now,
    }


async def update_cart_mandate_surface_status_in_session(
    *,
    session_id: str,
    cart_mandate_id: str,
    new_status: str,
    db_path: Path | str | None = None,
) -> None:
    """Update any previously rendered `AP2CartMandateApprovalCard` status inside `session_ui_turns`."""
    if not session_id:
        return
    async with connect_domain_db(db_path) as db:
        cur = await db.execute(
            "SELECT turn_id, surfaces_json, hitl_mandate_json FROM session_ui_turns WHERE session_id = ?",
            (session_id,),
        )
        rows = await cur.fetchall()
        for row in rows:
            changed = False
            surfaces = json.loads(row["surfaces_json"] or "[]")
            for s in surfaces:
                if (
                    s.get("surface_type") == "AP2CartMandateApprovalCard"
                    and s.get("payload", {}).get("cart_mandate_id") == cart_mandate_id
                ):
                    s["payload"]["status"] = new_status
                    changed = True
            hitl = (
                json.loads(row["hitl_mandate_json"])
                if row["hitl_mandate_json"]
                else None
            )
            if hitl and hitl.get("cart_mandate_id") == cart_mandate_id:
                hitl["status"] = new_status
                changed = True
            if changed:
                await db.execute(
                    "UPDATE session_ui_turns SET surfaces_json = ?, hitl_mandate_json = ? WHERE turn_id = ?",
                    (
                        json.dumps(surfaces),
                        json.dumps(hitl) if hitl else None,
                        row["turn_id"],
                    ),
                )
        await db.commit()


async def list_session_ui_turns(
    session_id: str,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Retrieve ordered chat turns and A2UI surfaces for a session."""
    async with connect_domain_db(db_path) as db:
        cur = await db.execute(
            """
            SELECT turn_id, session_id, user_id, role, content,
                   surfaces_json, hitl_mandate_json, trace_id, created_at
            FROM session_ui_turns
            WHERE session_id = ?
            ORDER BY created_at ASC, rowid ASC
            """,
            (session_id,),
        )
        rows = await cur.fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["surfaces"] = json.loads(d.pop("surfaces_json") or "[]")
            raw_hitl = d.pop("hitl_mandate_json")
            d["hitl_mandate"] = json.loads(raw_hitl) if raw_hitl else None
            out.append(d)
        return out


# ---------------------------------------------------------------------------
# Telemetry Spans, Token FinOps & Skill Activation Ledger
# ---------------------------------------------------------------------------


_INSERT_SPANS_SQL = """
    INSERT OR REPLACE INTO telemetry_spans (
        span_id, trace_id, parent_span_id, session_id, service_name,
        agent_name, span_kind, name, start_time_ms, duration_ms,
        status, attributes_json
    ) VALUES (
        :span_id, :trace_id, :parent_span_id, :session_id, :service_name,
        :agent_name, :span_kind, :name, :start_time_ms, :duration_ms,
        :status, :attributes_json
    )
"""


def _write_spans_with_timeout(resolved: Path, batch: list[dict[str, Any]], timeout_ms: int) -> None:
    conn = sqlite3.connect(str(resolved), timeout=max(0.2, timeout_ms / 1000.0))
    try:
        conn.execute(f"PRAGMA busy_timeout={timeout_ms};")
        conn.executemany(_INSERT_SPANS_SQL, batch)
        conn.commit()
    finally:
        conn.close()


def insert_spans_sync(
    spans: Sequence[dict[str, Any]],
    db_path: Path | str | None = None,
) -> None:
    """Persist OpenTelemetry span rows without deadlocking active `aiosqlite` loop transactions."""
    if not spans:
        return
    resolved = _resolve_db(db_path)
    batch = [dict(s) for s in spans]
    try:
        _write_spans_with_timeout(resolved, batch, timeout_ms=150)
    except sqlite3.OperationalError:
        def _retry_async() -> None:
            for delay in (0.08, 0.20, 0.50):
                time.sleep(delay)
                try:
                    _write_spans_with_timeout(resolved, batch, timeout_ms=5000)
                    return
                except sqlite3.OperationalError:
                    continue

        threading.Thread(target=_retry_async, daemon=True).start()


async def record_token_usage(
    *,
    session_id: str,
    trace_id: str,
    span_id: str,
    service_name: str,
    agent_name: str,
    model_name: str,
    prompt_tokens: int,
    candidates_tokens: int,
    cached_tokens: int,
    total_tokens: int,
    estimated_cost_usd: float,
    latency_ms: float,
    db_path: Path | str | None = None,
) -> None:
    """Persist a model call token and FinOps cost entry."""
    now = datetime.now(timezone.utc).isoformat()
    async with connect_domain_db(db_path) as db:
        await db.execute(
            """
            INSERT INTO token_usage_ledger (
                session_id, trace_id, span_id, service_name, agent_name, model_name,
                prompt_tokens, candidates_tokens, cached_tokens, total_tokens,
                estimated_cost_usd, latency_ms, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                trace_id,
                span_id,
                service_name,
                agent_name,
                model_name,
                int(prompt_tokens),
                int(candidates_tokens),
                int(cached_tokens),
                int(total_tokens),
                float(estimated_cost_usd),
                round(float(latency_ms), 2),
                now,
            ),
        )
        await db.commit()


async def record_skill_activation(
    *,
    session_id: str,
    trace_id: str,
    service_name: str,
    agent_name: str,
    skill_name: str,
    db_path: Path | str | None = None,
) -> None:
    """Persist a lazy `SkillToolset` SOP activation (`load_skill`) across processes."""
    now = datetime.now(timezone.utc).isoformat()
    async with connect_domain_db(db_path) as db:
        await db.execute(
            """
            INSERT INTO skill_activation_ledger (
                session_id, trace_id, service_name, agent_name, skill_name, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, trace_id, service_name, agent_name, skill_name, now),
        )
        await db.commit()


async def get_recent_skill_activations_since(
    session_id: str,
    since_id: int = 0,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Fetch skill activations recorded for `session_id` with `id > since_id`."""
    if not session_id:
        return []
    async with connect_domain_db(db_path) as db:
        cur = await db.execute(
            """
            SELECT id, session_id, trace_id, service_name, agent_name, skill_name, created_at
            FROM skill_activation_ledger
            WHERE session_id = ? AND id > ?
            ORDER BY id ASC
            """,
            (session_id, since_id),
        )
        return [dict(r) for r in await cur.fetchall()]


async def get_max_skill_activation_id(
    session_id: str,
    db_path: Path | str | None = None,
) -> int:
    """Return current max `id` in `skill_activation_ledger` for `session_id`."""
    if not session_id:
        return 0
    async with connect_domain_db(db_path) as db:
        cur = await db.execute(
            "SELECT COALESCE(MAX(id), 0) AS max_id FROM skill_activation_ledger WHERE session_id = ?",
            (session_id,),
        )
        row = await cur.fetchone()
        return int(row["max_id"]) if row else 0


async def get_telemetry_summary(
    session_id: str | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Aggregate token consumption, USD cost, A2A calls, tool executions, and skill activations."""
    async with connect_domain_db(db_path) as db:
        where_clause = "WHERE session_id = ?" if session_id else ""
        params: tuple[Any, ...] = (session_id,) if session_id else ()

        tok_cur = await db.execute(
            f"""
            SELECT
                COUNT(*) AS llm_calls,
                COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                COALESCE(SUM(candidates_tokens), 0) AS candidates_tokens,
                COALESCE(SUM(cached_tokens), 0) AS cached_tokens,
                COALESCE(SUM(total_tokens), 0) AS total_tokens,
                COALESCE(SUM(estimated_cost_usd), 0.0) AS total_cost_usd
            FROM token_usage_ledger
            {where_clause}
            """,
            params,
        )
        totals = dict(await tok_cur.fetchone())  # type: ignore[arg-type]

        by_agent_cur = await db.execute(
            f"""
            SELECT
                agent_name,
                model_name,
                COUNT(*) AS llm_calls,
                COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                COALESCE(SUM(candidates_tokens), 0) AS candidates_tokens,
                COALESCE(SUM(cached_tokens), 0) AS cached_tokens,
                COALESCE(SUM(total_tokens), 0) AS total_tokens,
                ROUND(COALESCE(SUM(estimated_cost_usd), 0.0), 8) AS cost_usd,
                ROUND(COALESCE(AVG(latency_ms), 0.0), 2) AS avg_latency_ms
            FROM token_usage_ledger
            {where_clause}
            GROUP BY agent_name, model_name
            ORDER BY total_tokens DESC
            """,
            params,
        )
        by_agent = [dict(r) for r in await by_agent_cur.fetchall()]

        skills_cur = await db.execute(
            f"""
            SELECT agent_name, skill_name, COUNT(*) AS activations, MAX(created_at) AS last_activated_at
            FROM skill_activation_ledger
            {where_clause}
            GROUP BY agent_name, skill_name
            ORDER BY last_activated_at ASC
            """,
            params,
        )
        skills = [dict(r) for r in await skills_cur.fetchall()]

        spans_cur = await db.execute(
            f"""
            SELECT name, service_name, agent_name, duration_ms, status
            FROM telemetry_spans
            {where_clause}
            """,
            params,
        )
        spans = [dict(r) for r in await spans_cur.fetchall()]

    tool_calls = [s for s in spans if s["name"].startswith("tool.")]
    a2a_calls = [
        s
        for s in spans
        if s["name"].startswith("a2a.") or s["name"] in (
            "tool.catalog_agent",
            "tool.policy_risk_agent",
            "tool.ap2_settlement_agent",
        )
    ]

    return {
        "session_id": session_id,
        "llm_calls": int(totals["llm_calls"]),
        "prompt_tokens": int(totals["prompt_tokens"]),
        "candidates_tokens": int(totals["candidates_tokens"]),
        "cached_tokens": int(totals["cached_tokens"]),
        "total_tokens": int(totals["total_tokens"]),
        "estimated_cost_usd": round(float(totals["total_cost_usd"]), 8),
        "tool_calls_count": len(tool_calls),
        "a2a_calls_count": len(a2a_calls),
        "spans_count": len(spans),
        "by_agent": by_agent,
        "activated_skills": skills,
    }


async def list_telemetry_spans(
    session_id: str | None = None,
    limit: int = 100,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Return ordered OpenTelemetry spans for waterfall rendering."""
    async with connect_domain_db(db_path) as db:
        if session_id:
            # Include spans sharing any trace_id seen in this session so cross-service A2A spans always link!
            cur = await db.execute(
                """
                SELECT * FROM telemetry_spans
                WHERE session_id = ?
                   OR trace_id IN (
                       SELECT DISTINCT trace_id FROM telemetry_spans WHERE session_id = ? AND trace_id != ''
                   )
                ORDER BY start_time_ms ASC
                LIMIT ?
                """,
                (session_id, session_id, int(limit)),
            )
        else:
            cur = await db.execute(
                """
                SELECT * FROM telemetry_spans
                ORDER BY start_time_ms DESC
                LIMIT ?
                """,
                (int(limit),),
            )
        rows = await cur.fetchall()
        results: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            try:
                d["attributes"] = json.loads(d.get("attributes_json") or "{}")
            except Exception:
                d["attributes"] = {}
            results.append(d)
        if not session_id:
            results.reverse()
        return results
