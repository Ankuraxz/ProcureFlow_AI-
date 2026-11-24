---
name: ap2-mandate-lifecycle
description: Cryptographic Agent Payments Protocol (AP2) specification for canonical JSON serialization, SHA-256 cart digests, Ed25519 verification, and three-mandate audit chains.
license: Apache-2.0
compatibility: procureflow-ai-settlement-slm
metadata:
  domain: settlement
  version: "1.0.0"
---

# AP2 (Agent Payments Protocol) Cryptographic Mandate Lifecycle

Load this playbook before verifying an `IntentMandate`, staging a `CartMandate`, or executing `PaymentMandate` settlement.

## 1. Three-Mandate Verifiable Chain

1. **`IntentMandate` (Standing Pre-Authorization)**:
   - Pre-signed by the user's Ed25519 keypair with explicit guardrails: `max_auto_usd = 500.0`, `allowed_tiers = ["PREFERRED", "APPROVED"]`, `max_budget_utilization = 0.80`.
   - Used ONLY for `AUTO_INTENT_MANDATE` settlements after verifying the Ed25519 signature and confirming all three bounds hold.
2. **`CartMandate` (Explicit Human-Signed Cart)**:
   - Staged with status `PENDING_SIGNATURE` whenever an order requires HITL escalation (`> $500`, `UNVERIFIED` vendor, or `>= 80%` post-order budget utilization).
   - Binds exact line items, quantities, prices, vendor, user, department, and total into a deterministic `cart_sha256` digest.
3. **`PaymentMandate` (Immutable Ledger Settlement Record)**:
   - Minted ONLY after cryptographic verification of either a valid `IntentMandate` (autonomous path) or a valid human-signed `CartMandate` (HITL path).
   - Atomically deducts department budget (`spent_usd`) and writes the settled order receipt.

## 2. Canonical JSON & `cart_sha256` Digest Specification

All digests and signed payloads MUST use deterministic canonical JSON (`sort_keys=True, separators=(",", ":")` with monetary floats rounded to 2 decimals):

1. **Cart Digest (`cart_sha256`)**:
   SHA-256 hex digest over canonical JSON of:
   `{"cart_mandate_id", "currency", "department_id", "line_items", "total_usd", "user_id", "vendor_id"}`
2. **Canonical Payload to Sign (`canonical_payload_to_sign`)**:
   Exact UTF-8 string of canonical JSON:
   `{"cart_mandate_id": ..., "cart_sha256": ..., "currency": "USD", "department_id": ..., "total_usd": ..., "user_id": ..., "vendor_id": ...}`
3. **Ed25519 Verification & Tamper Rejection**:
   - Raw 32-byte public keys and raw 64-byte signatures are encoded as standard Base64 (`public_key_b64`, `signature_b64`), matching WebCrypto `crypto.subtle.sign("Ed25519", ...)` byte-for-byte.
   - During settlement verification, ALWAYS recompute `cart_sha256` and `canonical_payload_to_sign` from stored database cart fields. Any post-staging modification to price, quantity, SKU, or vendor invalidates the signature and MUST abort settlement immediately.
