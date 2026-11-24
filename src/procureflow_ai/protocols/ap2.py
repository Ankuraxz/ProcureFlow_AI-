"""Agent Payments Protocol (AP2) cryptographic mandates, canonical digests, and Ed25519 verification."""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any, Literal
import uuid

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel, ConfigDict, Field


class AP2VerificationError(ValueError):
    """Raised when an AP2 mandate digest or Ed25519 signature fails verification."""


class CartLineItem(BaseModel):
    """Normalized line item bound inside a CartMandate digest."""

    model_config = ConfigDict(extra="ignore")

    sku_id: str
    name: str
    quantity: int = Field(ge=1)
    unit_price_usd: float = Field(ge=0.0)
    discount_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    line_total_usd: float = Field(ge=0.0)


class IntentMandate(BaseModel):
    """Standing user/department pre-authorization mandate signed with Ed25519."""

    intent_mandate_id: str
    user_id: str
    department_id: str
    max_auto_usd: float = 500.0
    allowed_tiers: list[str] = Field(default_factory=lambda: ["PREFERRED", "APPROVED"])
    max_budget_utilization: float = 0.80
    currency: str = "USD"
    canonical_payload: str
    public_key_b64: str
    signature_b64: str
    status: Literal["ACTIVE", "REVOKED", "EXPIRED"] = "ACTIVE"
    issued_at: str
    expires_at: str


class CartMandate(BaseModel):
    """Explicit human-reviewed AP2 CartMandate binding exact items, totals, and SHA-256 digest."""

    cart_mandate_id: str
    session_id: str = ""
    user_id: str
    department_id: str
    vendor_id: str
    vendor_name: str
    vendor_tier: str
    line_items: list[CartLineItem]
    total_usd: float
    currency: str = "USD"
    cart_sha256: str
    canonical_payload_to_sign: str
    escalation_reasons: list[str] = Field(default_factory=list)
    status: Literal["PENDING_SIGNATURE", "SIGNED_SETTLED", "REJECTED"] = (
        "PENDING_SIGNATURE"
    )
    public_key_b64: str | None = None
    signature_b64: str | None = None
    rejection_reason: str | None = None
    created_at: str
    expires_at: str
    decided_at: str | None = None


class PaymentMandate(BaseModel):
    """Immutable AP2 settlement proof chaining IntentMandate or CartMandate to ledger commit."""

    payment_mandate_id: str
    order_id: str
    authorization_mode: Literal["AUTO_INTENT_MANDATE", "HITL_CART_MANDATE"]
    intent_mandate_id: str | None = None
    cart_mandate_id: str | None = None
    user_id: str
    department_id: str
    vendor_id: str
    total_usd: float
    currency: str = "USD"
    cart_sha256: str
    settlement_digest_sha256: str
    signer_public_key_b64: str
    source_signature_b64: str
    settlement_signature_b64: str
    status: Literal["SETTLED"] = "SETTLED"
    settled_at: str


def canonical_json(data: Any) -> str:
    """Serialize data deterministically with sorted keys and compact separators."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(payload: str | bytes) -> str:
    """Compute lowercase hexadecimal SHA-256 digest."""
    raw = payload.encode("utf-8") if isinstance(payload, str) else payload
    return hashlib.sha256(raw).hexdigest()


def generate_ed25519_keypair() -> tuple[str, str]:
    """Generate a random Ed25519 keypair as `(private_key_b64, public_key_b64)` raw 32-byte keys."""
    priv = Ed25519PrivateKey.generate()
    priv_bytes = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_bytes = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return (
        base64.b64encode(priv_bytes).decode("ascii"),
        base64.b64encode(pub_bytes).decode("ascii"),
    )


def derive_deterministic_ed25519_keypair(seed_label: str) -> tuple[str, str]:
    """Derive a repeatable 32-byte Ed25519 keypair `(private_key_b64, public_key_b64)` for personas/authority."""
    seed_bytes = hashlib.sha256(f"procureflow-ap2-ed25519:{seed_label}".encode("utf-8")).digest()
    priv = Ed25519PrivateKey.from_private_bytes(seed_bytes)
    pub_bytes = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return (
        base64.b64encode(seed_bytes).decode("ascii"),
        base64.b64encode(pub_bytes).decode("ascii"),
    )


def sign_ed25519(private_key_b64: str, payload: str | bytes) -> str:
    """Sign UTF-8 string or bytes with a raw 32-byte Base64 Ed25519 private key.

    Produces a raw 64-byte Ed25519 signature encoded as standard Base64, matching
    WebCrypto `crypto.subtle.sign("Ed25519", privateKey, data)` byte-for-byte.
    """
    priv_bytes = base64.b64decode(private_key_b64)
    if len(priv_bytes) != 32:
        raise AP2VerificationError(
            f"Ed25519 private key must be 32 raw bytes, got {len(priv_bytes)}"
        )
    priv = Ed25519PrivateKey.from_private_bytes(priv_bytes)
    msg_bytes = payload.encode("utf-8") if isinstance(payload, str) else payload
    sig_bytes = priv.sign(msg_bytes)
    return base64.b64encode(sig_bytes).decode("ascii")


def verify_ed25519(
    public_key_b64: str,
    payload: str | bytes,
    signature_b64: str,
) -> bool:
    """Verify a Base64-encoded raw 64-byte Ed25519 signature against a raw 32-byte public key.

    Matches WebCrypto `exportKey("raw", publicKey)` and `crypto.subtle.sign("Ed25519", ...)`
    byte-for-byte. Returns False on any decoding, key-length, or cryptographic mismatch.
    """
    if not public_key_b64 or not signature_b64:
        return False
    try:
        pub_bytes = base64.b64decode(public_key_b64, validate=True)
        sig_bytes = base64.b64decode(signature_b64, validate=True)
    except Exception:
        return False
    if len(pub_bytes) != 32 or len(sig_bytes) != 64:
        return False
    msg_bytes = payload.encode("utf-8") if isinstance(payload, str) else payload
    try:
        pub = Ed25519PublicKey.from_public_bytes(pub_bytes)
        pub.verify(sig_bytes, msg_bytes)
        return True
    except InvalidSignature:
        return False
    except Exception:
        return False


def normalize_line_items(
    line_items: Sequence[Mapping[str, Any] | CartLineItem],
) -> list[dict[str, Any]]:
    """Normalize cart line items into canonical dictionaries for deterministic hashing."""
    normalized: list[dict[str, Any]] = []
    for raw in line_items:
        item = raw if isinstance(raw, CartLineItem) else CartLineItem.model_validate(raw)
        normalized.append(
            {
                "sku_id": item.sku_id,
                "name": item.name,
                "quantity": int(item.quantity),
                "unit_price_usd": round(float(item.unit_price_usd), 2),
                "discount_pct": round(float(item.discount_pct), 2),
                "line_total_usd": round(float(item.line_total_usd), 2),
            }
        )
    return normalized


def compute_cart_sha256(
    *,
    cart_mandate_id: str,
    user_id: str,
    department_id: str,
    vendor_id: str,
    currency: str,
    total_usd: float,
    line_items: Sequence[Mapping[str, Any] | CartLineItem],
) -> str:
    """Compute canonical SHA-256 digest over exact cart fields and line items."""
    digest_envelope = {
        "cart_mandate_id": cart_mandate_id,
        "user_id": user_id,
        "department_id": department_id,
        "vendor_id": vendor_id,
        "currency": currency,
        "total_usd": round(float(total_usd), 2),
        "line_items": normalize_line_items(line_items),
    }
    return sha256_hex(canonical_json(digest_envelope))


def build_canonical_payload_to_sign(
    *,
    cart_mandate_id: str,
    user_id: str,
    department_id: str,
    vendor_id: str,
    total_usd: float,
    currency: str = "USD",
    cart_sha256: str,
) -> str:
    """Build the exact canonical JSON string signed by WebCrypto or CLI Ed25519 keys."""
    return canonical_json(
        {
            "cart_mandate_id": cart_mandate_id,
            "user_id": user_id,
            "department_id": department_id,
            "vendor_id": vendor_id,
            "total_usd": round(float(total_usd), 2),
            "currency": currency,
            "cart_sha256": cart_sha256,
        }
    )


def build_intent_canonical_payload(
    *,
    intent_mandate_id: str,
    user_id: str,
    department_id: str,
    max_auto_usd: float,
    allowed_tiers: Sequence[str],
    max_budget_utilization: float,
    currency: str = "USD",
) -> str:
    """Build deterministic canonical JSON for standing `IntentMandate` signatures."""
    return canonical_json(
        {
            "intent_mandate_id": intent_mandate_id,
            "user_id": user_id,
            "department_id": department_id,
            "max_auto_usd": round(float(max_auto_usd), 2),
            "allowed_tiers": sorted(str(t) for t in allowed_tiers),
            "max_budget_utilization": round(float(max_budget_utilization), 4),
            "currency": currency,
        }
    )


def create_signed_intent_mandate(
    *,
    intent_mandate_id: str,
    user_id: str,
    department_id: str,
    private_key_b64: str,
    public_key_b64: str,
    max_auto_usd: float = 500.0,
    allowed_tiers: Sequence[str] = ("PREFERRED", "APPROVED"),
    max_budget_utilization: float = 0.80,
    currency: str = "USD",
    issued_at: str | None = None,
    expires_at: str | None = None,
) -> IntentMandate:
    """Create and sign a standing user `IntentMandate`."""
    now = datetime.now(timezone.utc)
    issued = issued_at or now.isoformat()
    expires = expires_at or (now + timedelta(days=365)).isoformat()
    canonical_payload = build_intent_canonical_payload(
        intent_mandate_id=intent_mandate_id,
        user_id=user_id,
        department_id=department_id,
        max_auto_usd=max_auto_usd,
        allowed_tiers=allowed_tiers,
        max_budget_utilization=max_budget_utilization,
        currency=currency,
    )
    signature_b64 = sign_ed25519(private_key_b64, canonical_payload)
    return IntentMandate(
        intent_mandate_id=intent_mandate_id,
        user_id=user_id,
        department_id=department_id,
        max_auto_usd=round(float(max_auto_usd), 2),
        allowed_tiers=list(allowed_tiers),
        max_budget_utilization=round(float(max_budget_utilization), 4),
        currency=currency,
        canonical_payload=canonical_payload,
        public_key_b64=public_key_b64,
        signature_b64=signature_b64,
        status="ACTIVE",
        issued_at=issued,
        expires_at=expires,
    )


def verify_intent_mandate(mandate: Mapping[str, Any] | IntentMandate) -> tuple[bool, str]:
    """Recompute canonical payload from IntentMandate fields and verify its Ed25519 signature."""
    m = mandate if isinstance(mandate, IntentMandate) else IntentMandate.model_validate(mandate)
    if m.status != "ACTIVE":
        return False, f"IntentMandate status is {m.status}, expected ACTIVE"
    recomputed = build_intent_canonical_payload(
        intent_mandate_id=m.intent_mandate_id,
        user_id=m.user_id,
        department_id=m.department_id,
        max_auto_usd=m.max_auto_usd,
        allowed_tiers=m.allowed_tiers,
        max_budget_utilization=m.max_budget_utilization,
        currency=m.currency,
    )
    if recomputed != m.canonical_payload:
        return False, "INTENT_MANDATE_PAYLOAD_TAMPERED"
    if not verify_ed25519(m.public_key_b64, recomputed, m.signature_b64):
        return False, "INVALID_INTENT_MANDATE_SIGNATURE"
    return True, "VALID"


def build_cart_mandate(
    *,
    user_id: str,
    department_id: str,
    vendor_id: str,
    vendor_name: str,
    vendor_tier: str,
    line_items: Sequence[Mapping[str, Any] | CartLineItem],
    escalation_reasons: Sequence[str] = (),
    session_id: str = "",
    cart_mandate_id: str | None = None,
    currency: str = "USD",
) -> CartMandate:
    """Stage a new `CartMandate` with deterministic `cart_sha256` and `canonical_payload_to_sign`."""
    cm_id = cart_mandate_id or f"cm_{uuid.uuid4().hex[:12]}"
    normalized_dicts = normalize_line_items(line_items)
    total_usd = round(sum(item["line_total_usd"] for item in normalized_dicts), 2)
    cart_sha256 = compute_cart_sha256(
        cart_mandate_id=cm_id,
        user_id=user_id,
        department_id=department_id,
        vendor_id=vendor_id,
        currency=currency,
        total_usd=total_usd,
        line_items=normalized_dicts,
    )
    canonical_payload = build_canonical_payload_to_sign(
        cart_mandate_id=cm_id,
        user_id=user_id,
        department_id=department_id,
        vendor_id=vendor_id,
        total_usd=total_usd,
        currency=currency,
        cart_sha256=cart_sha256,
    )
    now = datetime.now(timezone.utc)
    return CartMandate(
        cart_mandate_id=cm_id,
        session_id=session_id,
        user_id=user_id,
        department_id=department_id,
        vendor_id=vendor_id,
        vendor_name=vendor_name,
        vendor_tier=vendor_tier,
        line_items=[CartLineItem.model_validate(i) for i in normalized_dicts],
        total_usd=total_usd,
        currency=currency,
        cart_sha256=cart_sha256,
        canonical_payload_to_sign=canonical_payload,
        escalation_reasons=list(escalation_reasons),
        status="PENDING_SIGNATURE",
        created_at=now.isoformat(),
        expires_at=(now + timedelta(hours=24)).isoformat(),
    )


def verify_stored_cart_mandate(
    *,
    cart_mandate_id: str,
    user_id: str,
    department_id: str,
    vendor_id: str,
    currency: str,
    total_usd: float,
    line_items: Sequence[Mapping[str, Any] | CartLineItem],
    stored_cart_sha256: str,
    stored_canonical_payload: str,
    signature_b64: str,
    public_key_b64: str,
) -> tuple[bool, str, str]:
    """Recompute `cart_sha256` & `canonical_payload_to_sign` from stored DB fields and verify Ed25519.

    Any tampering with stored `total_usd`, `line_items`, `quantity`, `unit_price_usd`, `vendor_id`,
    `user_id`, or `department_id` changes `recomputed_sha256` and `recomputed_payload`, causing
    verification to fail immediately.

    Returns:
        `(is_valid, reason_code, recomputed_sha256)`
    """
    recomputed_sha256 = compute_cart_sha256(
        cart_mandate_id=cart_mandate_id,
        user_id=user_id,
        department_id=department_id,
        vendor_id=vendor_id,
        currency=currency,
        total_usd=total_usd,
        line_items=line_items,
    )
    recomputed_payload = build_canonical_payload_to_sign(
        cart_mandate_id=cart_mandate_id,
        user_id=user_id,
        department_id=department_id,
        vendor_id=vendor_id,
        total_usd=total_usd,
        currency=currency,
        cart_sha256=recomputed_sha256,
    )
    if recomputed_sha256 != stored_cart_sha256:
        return False, "CART_DIGEST_TAMPERED", recomputed_sha256
    if recomputed_payload != stored_canonical_payload:
        return False, "CANONICAL_PAYLOAD_TAMPERED", recomputed_sha256
    if not verify_ed25519(public_key_b64, recomputed_payload, signature_b64):
        return False, "INVALID_ED25519_SIGNATURE", recomputed_sha256
    return True, "VALID", recomputed_sha256


def mint_payment_mandate(
    *,
    order_id: str,
    authorization_mode: Literal["AUTO_INTENT_MANDATE", "HITL_CART_MANDATE"],
    user_id: str,
    department_id: str,
    vendor_id: str,
    total_usd: float,
    cart_sha256: str,
    signer_public_key_b64: str,
    source_signature_b64: str,
    intent_mandate_id: str | None = None,
    cart_mandate_id: str | None = None,
    currency: str = "USD",
) -> PaymentMandate:
    """Mint a cryptographically chained `PaymentMandate` signed by the settlement authority."""
    pm_id = f"pm_{uuid.uuid4().hex[:12]}"
    settled_at = datetime.now(timezone.utc).isoformat()
    authority_priv_b64, _ = derive_deterministic_ed25519_keypair("settlement_authority_v1")
    settlement_envelope = canonical_json(
        {
            "payment_mandate_id": pm_id,
            "order_id": order_id,
            "authorization_mode": authorization_mode,
            "intent_mandate_id": intent_mandate_id,
            "cart_mandate_id": cart_mandate_id,
            "user_id": user_id,
            "department_id": department_id,
            "vendor_id": vendor_id,
            "total_usd": round(float(total_usd), 2),
            "currency": currency,
            "cart_sha256": cart_sha256,
            "source_signature_b64": source_signature_b64,
            "settled_at": settled_at,
        }
    )
    settlement_digest = sha256_hex(settlement_envelope)
    settlement_sig = sign_ed25519(authority_priv_b64, settlement_envelope)
    return PaymentMandate(
        payment_mandate_id=pm_id,
        order_id=order_id,
        authorization_mode=authorization_mode,
        intent_mandate_id=intent_mandate_id,
        cart_mandate_id=cart_mandate_id,
        user_id=user_id,
        department_id=department_id,
        vendor_id=vendor_id,
        total_usd=round(float(total_usd), 2),
        currency=currency,
        cart_sha256=cart_sha256,
        settlement_digest_sha256=settlement_digest,
        signer_public_key_b64=signer_public_key_b64,
        source_signature_b64=source_signature_b64,
        settlement_signature_b64=settlement_sig,
        status="SETTLED",
        settled_at=settled_at,
    )
