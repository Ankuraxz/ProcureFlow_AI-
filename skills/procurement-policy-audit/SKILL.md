---
name: procurement-policy-audit
description: Corporate procurement compliance rules covering vendor trust tiers, anti-split-order detection, and department budget utilization warning bands.
license: Apache-2.0
compatibility: procureflow-ai-policy-slm
metadata:
  domain: policy
  version: "1.0.0"
---

# Enterprise Procurement Policy & FinOps Audit SOP

Use this playbook before clearing any purchase quote for settlement. Always verify live SQLite figures using `get_department_budget_status` and `audit_vendor_and_order_policy`.

## 1. Four-Tier Vendor Trust Classification

| Vendor Tier | Policy Meaning | Settlement Eligibility |
| :--- | :--- | :--- |
| **`PREFERRED`** | Master MSA + SOC2 Type II + negotiated SLA on file | Eligible for autonomous `IntentMandate` (`<= $500` and `< 80%` budget util) |
| **`APPROVED`** | Vetted B2B supplier with active W-9 / tax & security review | Eligible for autonomous `IntentMandate` (`<= $500` and `< 80%` budget util) |
| **`UNVERIFIED`** | Marketplace / third-party seller without completed security onboarding | **Mandatory `HITL_CART_MANDATE` escalation** regardless of dollar amount (even under `$500`!) |
| **`BLOCKED`** | Sanctioned, grey-market, or counterfeit-flagged supplier | **Immediate `HARD_BLOCK`** — never stage or settle any order |

## 2. Department Budget Utilization Bands

Compute post-order utilization precisely:
```text
projected_spent_usd = current_spent_usd + order_total_usd
projected_utilization_pct = round((projected_spent_usd / budget_total_usd) * 100.0, 2)
```

- **`HEALTHY` (`projected_utilization_pct < 80.0%`)**: Within normal operating envelope.
- **`WARNING_OVER_80` (`80.0% <= projected_utilization_pct <= 100.0%`)**: Crosses the 80% FinOps warning threshold. **Requires mandatory `HITL_CART_MANDATE` human sign-off** even when `order_total_usd <= $500.00` and vendor is `PREFERRED`/`APPROVED`.
- **`EXCEEDED` (`projected_utilization_pct > 100.0%`)**: Insufficient remaining department budget. **Immediate `HARD_BLOCK`**.

## 3. Anti-Split-Order Governance Check

To prevent employees or agents from splitting a `$600` purchase into two `$300` orders to bypass the `$500` autonomous limit:
- Inspect trailing 24-hour settled spend with the same vendor for the same user.
- If `recent_24h_vendor_spend_usd + order_total_usd > $500.00` while `order_total_usd <= $500.00` and `recent_24h_vendor_spend_usd > 0`, flag `"ANTI_SPLIT_ORDER_THRESHOLD_EXCEEDED"` and escalate to `HITL_CART_MANDATE`.
