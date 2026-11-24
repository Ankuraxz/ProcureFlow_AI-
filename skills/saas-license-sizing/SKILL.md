---
name: saas-license-sizing
description: Enterprise SaaS seat bundle sizing, annual vs monthly proration formulas, and mandatory SAML/OIDC SSO security readiness rules.
license: Apache-2.0
compatibility: procureflow-ai-catalog-slm
metadata:
  domain: catalog
  version: "1.0.0"
---

# SaaS License Sizing & Enterprise Readiness Playbook

Load this playbook whenever quoting software subscriptions, developer tools, or collaborative SaaS seats (e.g., Figma, GitHub Copilot, Linear).

## 1. Seat Bundle Discount Tiers

SaaS seat orders qualify for enterprise multi-seat tiering:
- **1–2 seats**: `0.0%` discount
- **3–4 seats**: `5.0%` multi-seat discount
- **5–9 seats**: `10.0%` team bundle discount
- **10+ seats**: `15.0%` enterprise volume discount

Always call `calculate_bundle_quote` with the requested `quantity` and `billing_period` (`"annual"` or `"monthly"`).

## 2. Annual vs. Monthly Proration Rules

- Catalog SaaS `unit_price_usd` values represent **annual per-seat rates** by default.
- **Annual Commitment (`billing_period="annual"`)**: Uses full catalog annual rate minus applicable seat bundle discount. Recommended for steady headcount to avoid monthly billing uplift.
- **Monthly / Prorated Term (`billing_period="monthly"`)**: Monthly equivalent is `(annual_unit_price / 12.0) * 1.10` (10% flexible-term uplift), rounded to 2 decimal places.

## 3. Mandatory Enterprise SSO & Security Gate

1. Verify `sso_supported == True` (SAML 2.0 / OIDC SCIM provisioning) for any SaaS seat deployment of **3 or more seats** or for Engineering / Security departments.
2. Source SaaS seats exclusively through `PREFERRED` or `APPROVED` software resellers (e.g., `vnd_cloudsoft` — CloudSoft SaaS Reseller).
3. Clearly report per-seat unit cost, applied bundle discount percentage, billing cadence, and SSO readiness in the quote summary returned to the orchestrator.
