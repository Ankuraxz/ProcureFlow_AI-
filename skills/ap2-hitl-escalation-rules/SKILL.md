---
name: ap2-hitl-escalation-rules
description: Deterministic AP2 governance matrix mapping order total, vendor tier, and post-order budget utilization to AUTO_INTENT_MANDATE, HITL_CART_MANDATE, or HARD_BLOCK.
license: Apache-2.0
compatibility: procureflow-ai-policy-slm
metadata:
  domain: policy
  version: "1.0.0"
---

# AP2 Conditional HITL Escalation Decision Matrix

Use this deterministic rule table to classify every procurement request into exactly one governance route. Never guess or relax a boundary condition.

## 1. Exact Three-Way Governance Classification

Evaluate rules in strict priority order (`HARD_BLOCK` -> `HITL_CART_MANDATE` -> `AUTO_INTENT_MANDATE`):

### Route A — `HARD_BLOCK` (Immediate Rejection)
Triggered when **ANY** of the following holds:
- `vendor_tier == "BLOCKED"`
- `projected_utilization_pct > 100.0` (order exceeds remaining department budget)

**Required Action**: Reject the transaction immediately. Do **NOT** stage a `CartMandate` and do **NOT** call settlement. Offer compliant catalog alternatives if available.

### Route B — `HITL_CART_MANDATE` (Pause for Human Ed25519 Signature)
Triggered when NOT `HARD_BLOCK` (`vendor_tier != "BLOCKED"` AND `projected_utilization_pct <= 100.0`), and **ANY** of the following holds:
- `total_usd > 500.0` (exceeds standing `IntentMandate` autonomous cap)
- `vendor_tier == "UNVERIFIED"` (unverified marketplace supplier requires explicit human risk acceptance)
- `80.0 <= projected_utilization_pct <= 100.0` (crosses the 80% department FinOps budget warning band)
- Anti-split-order cumulative 24h vendor spend exceeds `$500.0`

**Required Action**:
1. Record every applicable reason code in `escalation_reasons` (e.g., `"ORDER_OVER_500_AUTO_LIMIT"`, `"UNVERIFIED_VENDOR_TIER"`, `"BUDGET_UTILIZATION_GE_80_PCT"`).
2. Stage a `CartMandate` via `ap2_settlement_agent` (`stage_cart_mandate`).
3. Render `AP2CartMandateApprovalCard` and pause via `request_ap2_human_signature` until the human reviewer signs with Ed25519.

### Route C — `AUTO_INTENT_MANDATE` (Zero-Touch Autonomous Settlement)
Triggered **ONLY** when **ALL** of the following hold simultaneously:
- `total_usd <= 500.0`
- `vendor_tier in ("PREFERRED", "APPROVED")`
- `projected_utilization_pct < 80.0`
- Active Ed25519-signed `IntentMandate` is valid for the user.

**Required Action**: Authorize autonomous settlement via `ap2_settlement_agent` (`execute_ap2_settlement` with `authorization_mode="AUTO_INTENT_MANDATE"`) and emit an `OrderReceiptCard`.
