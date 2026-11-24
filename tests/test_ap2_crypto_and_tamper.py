"""Cryptographic AP2 Ed25519 mandate verification, settlement atomicity, and tamper-rejection tests."""

from __future__ import annotations

import base64
import json
from pathlib import Path
import sqlite3

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
import pytest

from procureflow_ai.db.queries import (
    get_cart_mandate_record,
    get_persona_context,
    list_orders,
    verify_and_settle_cart_mandate,
)
from procureflow_ai.protocols.ap2 import (
    AP2VerificationError,
    derive_deterministic_ed25519_keypair,
    generate_ed25519_keypair,
    sign_ed25519,
    verify_ed25519,
    verify_stored_cart_mandate,
)
from procureflow_ai.tools.settlement_tools import (
    check_active_intent_mandate,
    execute_ap2_settlement,
    reject_cart_mandate,
    stage_cart_mandate,
)


def _get_department_spent(db_path: Path, department_id: str) -> float:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT spent_usd FROM departments WHERE department_id = ?",
            (department_id,),
        ).fetchone()
        assert row is not None
        return float(row[0])
    finally:
        conn.close()


def _count_rows(db_path: Path, table_name: str) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])
    finally:
        conn.close()


def test_raw_32byte_ed25519_keys_and_64byte_webcrypto_interop() -> None:
    """Verify raw 32-byte Ed25519 keys and 64-byte Base64 signatures match RFC 8032 / WebCrypto format."""
    priv_b64, pub_b64 = generate_ed25519_keypair()
    priv_raw = base64.b64decode(priv_b64, validate=True)
    pub_raw = base64.b64decode(pub_b64, validate=True)
    assert len(priv_raw) == 32
    assert len(pub_raw) == 32

    # Sign via ProcureFlow helper and verify with raw cryptography Ed25519PublicKey
    payload = '{"cart_mandate_id":"cm_test","total_usd":2399.0}'
    sig_b64 = sign_ed25519(priv_b64, payload)
    sig_raw = base64.b64decode(sig_b64, validate=True)
    assert len(sig_raw) == 64

    webcrypto_pub = Ed25519PublicKey.from_public_bytes(pub_raw)
    webcrypto_pub.verify(sig_raw, payload.encode("utf-8"))
    assert verify_ed25519(pub_b64, payload, sig_b64) is True

    # Sign with raw Ed25519PrivateKey (exact WebCrypto subtle.sign behavior) and verify in ProcureFlow
    ext_priv = Ed25519PrivateKey.generate()
    ext_pub_raw = ext_priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    ext_pub_b64 = base64.b64encode(ext_pub_raw).decode("ascii")
    ext_sig_b64 = base64.b64encode(ext_priv.sign(payload.encode("utf-8"))).decode("ascii")

    assert verify_ed25519(ext_pub_b64, payload, ext_sig_b64) is True
    assert verify_ed25519(ext_pub_b64, payload + " ", ext_sig_b64) is False


@pytest.mark.asyncio
async def test_stage_sign_and_settle_cart_mandate_end_to_end(
    domain_db_path: Path,
) -> None:
    """Verify staging a CartMandate, signing canonical_payload_to_sign, and settling PaymentMandate."""
    persona = await get_persona_context("maya_lin", db_path=domain_db_path)
    assert persona is not None
    initial_spent = _get_department_spent(domain_db_path, "dept_ml_rnd")
    assert initial_spent == 6500.0

    line_items = [
        {
            "sku_id": "sku_mbp14_m4",
            "name": "Apple MacBook Pro 14-inch (M4 Pro, 24GB, 1TB)",
            "quantity": 2,
            "unit_price_usd": 2399.0,
            "discount_pct": 0.0,
            "line_total_usd": 4798.0,
        }
    ]

    staged = await stage_cart_mandate(
        user_id="maya_lin",
        vendor_id="vnd_apple_ent",
        line_items_json=json.dumps(line_items),
        escalation_reasons_json=json.dumps(["ORDER_OVER_500_AUTO_LIMIT"]),
        session_id="sess_hitl_ok",
    )
    assert staged["status"] == "PENDING_SIGNATURE"
    cm = staged["cart_mandate"]
    cm_id = cm["cart_mandate_id"]
    assert cm["total_usd"] == 4798.0
    assert len(cm["cart_sha256"]) == 64

    # Staging alone MUST NOT deduct department budget or create orders
    assert _get_department_spent(domain_db_path, "dept_ml_rnd") == 6500.0
    assert _count_rows(domain_db_path, "orders") == 0

    sig_b64 = sign_ed25519(
        persona["default_private_key_b64"],
        cm["canonical_payload_to_sign"],
    )
    settled = await execute_ap2_settlement(
        user_id="maya_lin",
        authorization_mode="HITL_CART_MANDATE",
        cart_mandate_id=cm_id,
        signature_b64=sig_b64,
        public_key_b64=persona["public_key_b64"],
        session_id="sess_hitl_ok",
    )
    assert settled["status"] == "SETTLED"
    receipt = settled["receipt"]
    assert receipt["authorization_mode"] == "HITL_CART_MANDATE"
    assert receipt["cart_mandate_id"] == cm_id
    assert receipt["payment_mandate_id"].startswith("pm_")
    assert receipt["total_usd"] == 4798.0

    # Budget atomically deducted and PaymentMandate signature cryptographically valid
    assert _get_department_spent(domain_db_path, "dept_ml_rnd") == 11298.0
    orders = await list_orders(user_id="maya_lin", db_path=domain_db_path)
    assert len(orders) == 1
    _, authority_pub_b64 = derive_deterministic_ed25519_keypair("settlement_authority_v1")
    assert len(base64.b64decode(orders[0]["settlement_signature_b64"])) == 64
    assert orders[0]["signer_public_key_b64"] == persona["public_key_b64"]
    assert len(base64.b64decode(authority_pub_b64)) == 32


@pytest.mark.asyncio
async def test_tamper_rejection_mutated_total_and_line_items_leave_budget_untouched(
    domain_db_path: Path,
) -> None:
    """MANDATORY TAMPER TEST 1: Mutating total_usd or line_items_json in SQLite after signing MUST fail."""
    persona = await get_persona_context("maya_lin", db_path=domain_db_path)
    assert persona is not None
    baseline_spent = _get_department_spent(domain_db_path, "dept_ml_rnd")
    assert baseline_spent == 6500.0

    # --- Case 1A: Mutate total_usd in SQLite after human signature ---
    staged_a = await stage_cart_mandate(
        user_id="maya_lin",
        vendor_id="vnd_apple_ent",
        line_items_json=json.dumps(
            [
                {
                    "sku_id": "sku_mbp14_m4",
                    "name": "Apple MacBook Pro 14-inch (M4 Pro, 24GB, 1TB)",
                    "quantity": 1,
                    "unit_price_usd": 2399.0,
                    "discount_pct": 0.0,
                    "line_total_usd": 2399.0,
                }
            ]
        ),
        escalation_reasons_json='["ORDER_OVER_500_AUTO_LIMIT"]',
    )
    cm_a = staged_a["cart_mandate"]
    valid_sig_a = sign_ed25519(
        persona["default_private_key_b64"],
        cm_a["canonical_payload_to_sign"],
    )

    # Tamper directly in SQLite: inflate total_usd from $2,399.00 to $9,999.00
    conn = sqlite3.connect(str(domain_db_path))
    try:
        conn.execute(
            "UPDATE ap2_cart_mandates SET total_usd = 9999.00 WHERE cart_mandate_id = ?",
            (cm_a["cart_mandate_id"],),
        )
        conn.commit()
    finally:
        conn.close()

    tampered_row_a = await get_cart_mandate_record(
        cm_a["cart_mandate_id"], db_path=domain_db_path
    )
    assert tampered_row_a is not None
    is_valid_a, reason_a, _ = verify_stored_cart_mandate(
        cart_mandate_id=tampered_row_a["cart_mandate_id"],
        user_id=tampered_row_a["user_id"],
        department_id=tampered_row_a["department_id"],
        vendor_id=tampered_row_a["vendor_id"],
        currency=tampered_row_a["currency"],
        total_usd=float(tampered_row_a["total_usd"]),
        line_items=tampered_row_a["line_items"],
        stored_cart_sha256=tampered_row_a["cart_sha256"],
        stored_canonical_payload=tampered_row_a["canonical_payload_to_sign"],
        signature_b64=valid_sig_a,
        public_key_b64=persona["public_key_b64"],
    )
    assert is_valid_a is False
    assert reason_a == "CART_DIGEST_TAMPERED"

    res_a = await execute_ap2_settlement(
        user_id="maya_lin",
        authorization_mode="HITL_CART_MANDATE",
        cart_mandate_id=cm_a["cart_mandate_id"],
        signature_b64=valid_sig_a,
    )
    assert res_a["status"] == "VERIFICATION_FAILED"
    assert "CART_DIGEST_TAMPERED" in res_a["error"]

    with pytest.raises(AP2VerificationError, match="CART_DIGEST_TAMPERED"):
        await verify_and_settle_cart_mandate(
            cart_mandate_id=cm_a["cart_mandate_id"],
            user_id="maya_lin",
            signature_b64=valid_sig_a,
            db_path=domain_db_path,
        )

    assert _get_department_spent(domain_db_path, "dept_ml_rnd") == baseline_spent
    assert _count_rows(domain_db_path, "orders") == 0
    assert _count_rows(domain_db_path, "ap2_payment_mandates") == 0

    # --- Case 1B: Mutate line_items_json (swap SKU while keeping total_usd identical) ---
    staged_b = await stage_cart_mandate(
        user_id="maya_lin",
        vendor_id="vnd_dell_premier",
        line_items_json=json.dumps(
            [
                {
                    "sku_id": "sku_dell_u3223qe",
                    "name": "Dell UltraSharp 32 4K USB-C Hub Monitor (U3223QE)",
                    "quantity": 1,
                    "unit_price_usd": 899.0,
                    "discount_pct": 0.0,
                    "line_total_usd": 899.0,
                }
            ]
        ),
        escalation_reasons_json='["ORDER_OVER_500_AUTO_LIMIT"]',
    )
    cm_b = staged_b["cart_mandate"]
    valid_sig_b = sign_ed25519(
        persona["default_private_key_b64"],
        cm_b["canonical_payload_to_sign"],
    )

    swapped_items = [
        {
            "sku_id": "sku_swapped_attacker_item",
            "name": "Unauthorized Gift Card Bundle",
            "quantity": 1,
            "unit_price_usd": 899.0,
            "discount_pct": 0.0,
            "line_total_usd": 899.0,
        }
    ]
    conn = sqlite3.connect(str(domain_db_path))
    try:
        conn.execute(
            "UPDATE ap2_cart_mandates SET line_items_json = ? WHERE cart_mandate_id = ?",
            (json.dumps(swapped_items), cm_b["cart_mandate_id"]),
        )
        conn.commit()
    finally:
        conn.close()

    res_b = await execute_ap2_settlement(
        user_id="maya_lin",
        authorization_mode="HITL_CART_MANDATE",
        cart_mandate_id=cm_b["cart_mandate_id"],
        signature_b64=valid_sig_b,
    )
    assert res_b["status"] == "VERIFICATION_FAILED"
    assert "CART_DIGEST_TAMPERED" in res_b["error"]
    assert _get_department_spent(domain_db_path, "dept_ml_rnd") == baseline_spent
    assert _count_rows(domain_db_path, "orders") == 0


@pytest.mark.asyncio
async def test_tamper_rejection_attacker_ed25519_keypair_leaves_budget_untouched(
    domain_db_path: Path,
) -> None:
    """MANDATORY TAMPER TEST 2: Signing with an attacker Ed25519 keypair MUST reject settlement."""
    baseline_spent = _get_department_spent(domain_db_path, "dept_ml_rnd")

    staged = await stage_cart_mandate(
        user_id="maya_lin",
        vendor_id="vnd_apple_ent",
        line_items_json=json.dumps(
            [
                {
                    "sku_id": "sku_apple_studio_display",
                    "name": "Apple Studio Display 27-inch 5K USB-C",
                    "quantity": 1,
                    "unit_price_usd": 1599.0,
                    "discount_pct": 0.0,
                    "line_total_usd": 1599.0,
                }
            ]
        ),
        escalation_reasons_json='["ORDER_OVER_500_AUTO_LIMIT"]',
    )
    cm = staged["cart_mandate"]

    attacker_priv_b64, attacker_pub_b64 = generate_ed25519_keypair()
    attacker_sig_b64 = sign_ed25519(attacker_priv_b64, cm["canonical_payload_to_sign"])

    # Attempt A: Attacker signature verified against maya_lin's registered public key
    forged_attempt = await execute_ap2_settlement(
        user_id="maya_lin",
        authorization_mode="HITL_CART_MANDATE",
        cart_mandate_id=cm["cart_mandate_id"],
        signature_b64=attacker_sig_b64,
    )
    assert forged_attempt["status"] == "VERIFICATION_FAILED"
    assert "INVALID_ED25519_SIGNATURE" in forged_attempt["error"]

    # Attempt B: Attacker supplies their own public key alongside their signature
    key_swap_attempt = await execute_ap2_settlement(
        user_id="maya_lin",
        authorization_mode="HITL_CART_MANDATE",
        cart_mandate_id=cm["cart_mandate_id"],
        signature_b64=attacker_sig_b64,
        public_key_b64=attacker_pub_b64,
    )
    assert key_swap_attempt["status"] == "VERIFICATION_FAILED"
    assert "UNTRUSTED_SIGNER_PUBLIC_KEY" in key_swap_attempt["error"]

    assert _get_department_spent(domain_db_path, "dept_ml_rnd") == baseline_spent
    assert _count_rows(domain_db_path, "orders") == 0
    assert _count_rows(domain_db_path, "ap2_payment_mandates") == 0


@pytest.mark.asyncio
async def test_anti_replay_and_idempotency_on_settled_and_rejected_mandates(
    domain_db_path: Path,
) -> None:
    """MANDATORY TAMPER TEST 3: Replaying settled/cross-mandate/rejected CartMandates never double-charges."""
    persona = await get_persona_context("maya_lin", db_path=domain_db_path)
    assert persona is not None
    baseline_spent = _get_department_spent(domain_db_path, "dept_ml_rnd")

    item_payload = [
        {
            "sku_id": "sku_dell_u3223qe",
            "name": "Dell UltraSharp 32 4K USB-C Hub Monitor (U3223QE)",
            "quantity": 1,
            "unit_price_usd": 899.0,
            "discount_pct": 0.0,
            "line_total_usd": 899.0,
        }
    ]

    # 1. Settle CartMandate #1 legitimately
    staged_1 = await stage_cart_mandate(
        user_id="maya_lin",
        vendor_id="vnd_dell_premier",
        line_items_json=json.dumps(item_payload),
        escalation_reasons_json='["ORDER_OVER_500_AUTO_LIMIT"]',
    )
    cm_1 = staged_1["cart_mandate"]
    sig_1 = sign_ed25519(
        persona["default_private_key_b64"],
        cm_1["canonical_payload_to_sign"],
    )
    first_settle = await execute_ap2_settlement(
        user_id="maya_lin",
        authorization_mode="HITL_CART_MANDATE",
        cart_mandate_id=cm_1["cart_mandate_id"],
        signature_b64=sig_1,
    )
    assert first_settle["status"] == "SETTLED"
    spent_after_first = _get_department_spent(domain_db_path, "dept_ml_rnd")
    assert spent_after_first == baseline_spent + 899.0
    assert _count_rows(domain_db_path, "orders") == 1
    assert _count_rows(domain_db_path, "ap2_payment_mandates") == 1

    # 2. Replay exact same settled CartMandate #1 -> idempotent (ZERO duplicate order or double budget deduction)
    replay_same = await execute_ap2_settlement(
        user_id="maya_lin",
        authorization_mode="HITL_CART_MANDATE",
        cart_mandate_id=cm_1["cart_mandate_id"],
        signature_b64=sig_1,
    )
    assert replay_same["receipt"]["order_id"] == first_settle["receipt"]["order_id"]
    assert _get_department_spent(domain_db_path, "dept_ml_rnd") == spent_after_first
    assert _count_rows(domain_db_path, "orders") == 1
    assert _count_rows(domain_db_path, "ap2_payment_mandates") == 1

    # 2b. Forged signature replay on already-settled CartMandate #1 MUST fail signature verification
    attacker_priv_b64, _ = generate_ed25519_keypair()
    forged_sig_1 = sign_ed25519(attacker_priv_b64, cm_1["canonical_payload_to_sign"])
    forged_settled_replay = await execute_ap2_settlement(
        user_id="maya_lin",
        authorization_mode="HITL_CART_MANDATE",
        cart_mandate_id=cm_1["cart_mandate_id"],
        signature_b64=forged_sig_1,
    )
    assert forged_settled_replay["status"] == "VERIFICATION_FAILED"
    assert "INVALID_ED25519_SIGNATURE" in forged_settled_replay["error"]

    # 3. Cross-mandate signature replay: replay sig_1 against identical items in newly staged CartMandate #2
    staged_2 = await stage_cart_mandate(
        user_id="maya_lin",
        vendor_id="vnd_dell_premier",
        line_items_json=json.dumps(item_payload),
        escalation_reasons_json='["ORDER_OVER_500_AUTO_LIMIT"]',
    )
    cm_2 = staged_2["cart_mandate"]
    assert cm_2["cart_mandate_id"] != cm_1["cart_mandate_id"]

    cross_replay = await execute_ap2_settlement(
        user_id="maya_lin",
        authorization_mode="HITL_CART_MANDATE",
        cart_mandate_id=cm_2["cart_mandate_id"],
        signature_b64=sig_1,
    )
    assert cross_replay["status"] == "VERIFICATION_FAILED"
    assert "INVALID_ED25519_SIGNATURE" in cross_replay["error"]
    assert _get_department_spent(domain_db_path, "dept_ml_rnd") == spent_after_first
    assert _count_rows(domain_db_path, "orders") == 1

    # 4. Reject CartMandate #2, then attempt to replay its genuine signature -> MUST reject
    sig_2 = sign_ed25519(
        persona["default_private_key_b64"],
        cm_2["canonical_payload_to_sign"],
    )
    rej = await reject_cart_mandate(
        cart_mandate_id=cm_2["cart_mandate_id"],
        user_id="maya_lin",
        reason="Cancelled monitor order",
    )
    assert rej["status"] == "REJECTED"

    post_reject_replay = await execute_ap2_settlement(
        user_id="maya_lin",
        authorization_mode="HITL_CART_MANDATE",
        cart_mandate_id=cm_2["cart_mandate_id"],
        signature_b64=sig_2,
    )
    assert post_reject_replay["status"] == "VERIFICATION_FAILED"
    assert "REJECTED" in post_reject_replay["error"]
    assert _get_department_spent(domain_db_path, "dept_ml_rnd") == spent_after_first


@pytest.mark.asyncio
async def test_autonomous_intent_mandate_settlement_and_tamper_rejection(
    domain_db_path: Path,
) -> None:
    """Verify standing IntentMandate autonomous settlement and SQLite cap-tamper rejection."""
    check = await check_active_intent_mandate(
        user_id="sam_chen",
        order_total_usd=156.75,
        vendor_tier="PREFERRED",
        projected_utilization_pct=21.31,
    )
    assert check["eligible_for_auto_settlement"] is True
    assert check["signature_verified"] is True

    # Tamper with max_auto_usd in ap2_intent_mandates without re-signing
    conn = sqlite3.connect(str(domain_db_path))
    try:
        conn.execute(
            "UPDATE ap2_intent_mandates SET max_auto_usd = 10000.0 WHERE user_id = 'sam_chen'"
        )
        conn.commit()
    finally:
        conn.close()

    tampered_auto = await execute_ap2_settlement(
        user_id="sam_chen",
        authorization_mode="AUTO_INTENT_MANDATE",
        vendor_id="vnd_yubico",
        line_items_json=json.dumps(
            [
                {
                    "sku_id": "sku_yubikey_5c",
                    "name": "YubiKey 5C NFC Enterprise Security Key",
                    "quantity": 3,
                    "unit_price_usd": 55.0,
                    "discount_pct": 5.0,
                    "line_total_usd": 156.75,
                }
            ]
        ),
    )
    assert tampered_auto["status"] == "VERIFICATION_FAILED"
    assert "INTENT_MANDATE_PAYLOAD_TAMPERED" in tampered_auto["error"]
    assert _get_department_spent(domain_db_path, "dept_sec") == 2400.0
