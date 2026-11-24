---
name: a2ui-surface-composer
description: Declarative A2UI surface composition guide defining when and how to emit VendorComparisonCard, BudgetGaugeCard, AP2CartMandateApprovalCard, and OrderReceiptCard widgets.
license: Apache-2.0
compatibility: procureflow-ai-orchestrator
metadata:
  domain: ui
  version: "1.0.0"
---

# Declarative A2UI Surface Composition Guide

Load this playbook whenever presenting catalog options, department budget impact, HITL approval mandates, or completed order receipts to the user. Always emit typed A2UI surfaces via `emit_a2ui_surface(surface_type, title, payload_json)` alongside a concise conversational summary.

## 1. Required Surfaces by Workflow Outcome

| Procurement Outcome | Required A2UI Surfaces to Emit |
| :--- | :--- |
| **Discovery / Quote Comparison** | `VendorComparisonCard` (+ `BudgetGaugeCard` if budget checked) |
| **Autonomous Settlement (`AUTO_INTENT_MANDATE`)** | `VendorComparisonCard` + `BudgetGaugeCard` + `OrderReceiptCard` |
| **HITL Escalation (`HITL_CART_MANDATE`)** | `VendorComparisonCard` + `BudgetGaugeCard` + `AP2CartMandateApprovalCard` (and call `request_ap2_human_signature`) |
| **Policy Rejection (`HARD_BLOCK`)** | `BudgetGaugeCard` (if budget exceeded) or `VendorComparisonCard` (showing compliant alternatives vs blocked item) — NEVER emit `AP2CartMandateApprovalCard` or `OrderReceiptCard` |

## 2. Exact `payload_json` Schemas

### A. `VendorComparisonCard`
```json
{
  "items": [
    {
      "sku_id": "sku_yubikey_5c",
      "name": "YubiKey 5C NFC Enterprise Security Key",
      "vendor_id": "vnd_yubico",
      "vendor_name": "Yubico Direct",
      "vendor_tier": "PREFERRED",
      "unit_price_usd": 55.0,
      "quantity": 4,
      "discount_pct": 5.0,
      "subtotal_usd": 209.0,
      "lead_time_days": 2,
      "specs": "FIDO2/WebAuthn USB-C + NFC hardware key",
      "recommended": true
    }
  ],
  "currency": "USD",
  "notes": "5% volume discount applied for 4 units."
}
```

### B. `BudgetGaugeCard`
```json
{
  "department_id": "dept_sec",
  "department_name": "Information Security",
  "budget_total_usd": 12000.0,
  "current_spent_usd": 2400.0,
  "order_amount_usd": 209.0,
  "projected_spent_usd": 2609.0,
  "current_utilization_pct": 20.0,
  "projected_utilization_pct": 21.74,
  "warning_threshold_pct": 80.0,
  "status": "HEALTHY"
}
```
(`status` MUST be `"HEALTHY"`, `"WARNING_OVER_80"`, or `"EXCEEDED"`.)

### C. `AP2CartMandateApprovalCard`
Pass the exact staged `CartMandate` object returned by `ap2_settlement_agent` (`cart_mandate_id`, `user_id`, `department_id`, `vendor_id`, `vendor_name`, `vendor_tier`, `line_items`, `total_usd`, `currency`, `cart_sha256`, `canonical_payload_to_sign`, `escalation_reasons`, `status`: `"PENDING_SIGNATURE"`, `expires_at`), and invoke `request_ap2_human_signature(cart_mandate_id=...)`.

### D. `OrderReceiptCard`
Pass the exact receipt object returned by `ap2_settlement_agent` (`order_id`, `status`: `"SETTLED"`, `authorization_mode`, `vendor_name`, `department_name`, `total_usd`, `line_items`, `intent_mandate_id`, `cart_mandate_id`, `payment_mandate_id`, `cart_sha256`, `signature_preview`, `estimated_delivery_days`, `created_at`).
