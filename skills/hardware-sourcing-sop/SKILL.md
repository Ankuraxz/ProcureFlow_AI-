---
name: hardware-sourcing-sop
description: Enterprise hardware sourcing playbook defining role-to-device specifications, deterministic volume discount tiers, and warehouse shipping SLA ranking rules.
license: Apache-2.0
compatibility: procureflow-ai-catalog-slm
metadata:
  domain: catalog
  version: "1.0.0"
---

# Hardware Sourcing Standard Operating Procedure (SOP)

Use this playbook whenever sourcing laptops, workstations, monitors, docks, security keys, or peripherals for an employee or department.

## 1. Role-to-Hardware Specification Matrix

Match the requester's role and historical episodic preferences to the right hardware tier without overspending:

| Employee Role / Persona | Recommended Compute Tier | Display & Peripheral Guidance | Target Lead Time |
| :--- | :--- | :--- | :--- |
| **ML R&D / AI Engineering** | Apple Silicon M4 Pro / M4 Max laptops (`>= 24GB` unified memory) or Dual-GPU AI Workstations | 27" 5K or 32" 4K USB-C Hub Monitor (`>= 90W` PD) | `<= 3` business days |
| **Software / Security Engineering** | Apple MacBook Pro 14" M4 Pro or Dell Precision Developer Laptop (`>= 24GB` RAM) + FIDO2 Hardware Security Keys (YubiKey 5C NFC) | 4K USB-C Monitor + Mechanical Keyboard / Precision Mouse | `<= 4` business days |
| **IT Operations / General Staff** | Standard Developer Laptop or Certified Accessories (USB-C Docks, Mice, Keyboards) | Single 4K Monitor + Certified Thunderbolt Dock | `<= 5` business days |

## 2. Deterministic Hardware Volume Discount Tiers

Hardware unit pricing follows strict per-SKU quantity discount brackets applied before settlement:

- **1–2 units**: `0.0%` volume discount (`multiplier = 1.00`)
- **3–4 units**: `5.0%` volume discount (`multiplier = 0.95`)
- **5+ units**: `10.0%` volume discount (`multiplier = 0.90`)

Formula:
```text
discounted_unit_price_usd = round(unit_price_usd * (1.0 - discount_pct / 100.0), 2)
line_total_usd = round(discounted_unit_price_usd * quantity, 2)
```
Always invoke `calculate_bundle_quote` rather than computing totals mentally.

## 3. Vendor Preference & Shipping SLA Ranking

When multiple catalog SKUs satisfy technical requirements, rank candidates in this exact order:
1. **Vendor Compliance Tier**: Prefer `PREFERRED` over `APPROVED`. Flag `UNVERIFIED` vendors explicitly (they trigger mandatory AP2 HITL review even under `$500`). Never recommend `BLOCKED` vendors (`HARD_BLOCK`).
2. **Shipping SLA (`lead_time_days`)**: Prefer SKUs meeting the requester's target delivery window (especially `<= 3 days` when requested or stored in episodic preferences).
3. **Total Landed Cost (`subtotal_usd`)**: Prefer the lowest post-discount bundle price among compliant options.
