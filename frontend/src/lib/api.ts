import type {
  A2UISurface,
  A2UISurfaceType,
  AgentActivityPill,
  AgentTelemetryBreakdown,
  AP2CartMandateApprovalData,
  BudgetGaugeData,
  CartLineItem,
  ChatMessage,
  DeliveryTimelineStep,
  GuidedScenario,
  OrderReceiptData,
  ParsedSSEEvent,
  Persona,
  ProcurementSession,
  TelemetrySpan,
  TelemetrySummary,
  VendorComparisonData,
  VendorComparisonOption,
  VendorTier,
} from '../types/procureflow';
import { parseSSEBlock, postSSEStream } from './sse';

export const GUIDED_SCENARIOS: GuidedScenario[] = [
  {
    id: 'auto-ap2',
    title: 'Auto AP2 (<= $500)',
    badge: 'AUTO SETTLE',
    badgeTone: 'emerald',
    description: 'Preferred vendor, < $500, < 80% budget — settles autonomously via IntentMandate.',
    prompt: 'Order 3 YubiKey 5C NFC security keys for our new engineers',
  },
  {
    id: 'high-value-hitl',
    title: 'High-Value HITL (> $500)',
    badge: 'HITL > $500',
    badgeTone: 'amber',
    description: 'Exceeds $500 IntentMandate cap — stages CartMandate for Ed25519 signature.',
    prompt:
      'Order 2 MacBook Pro 14 M4 laptops and 2 Dell 32-inch 4K monitors needed within 3 days',
  },
  {
    id: 'unverified-vendor-hitl',
    title: 'Unverified Vendor HITL',
    badge: 'VENDOR ESCALATION',
    badgeTone: 'indigo',
    description: 'Marketplace vendor is UNVERIFIED — triggers AP2 cryptographic human review.',
    prompt: 'Quote and order 2 USB-C docks from QuickGear Marketplace',
  },
  {
    id: 'budget-warning-hitl',
    title: 'Budget Warning (>= 80%)',
    badge: 'FINOPS >= 80%',
    badgeTone: 'amber',
    description: 'Pushes Q4 department spend past 80% threshold — requires FinOps sign-off.',
    prompt: 'Order 3 Logitech MX Master mice for IT Operations',
    suggestedDepartmentHint: 'IT Operations',
  },
  {
    id: 'blocked-vendor-guardrail',
    title: 'Blocked Vendor Guardrail',
    badge: 'HARD BLOCK',
    badgeTone: 'rose',
    description: 'Sanctioned/grey-market vendor is hard-blocked by Policy SLM before settlement.',
    prompt: 'Buy the refurbished GPU from GreyImport Electronics',
  },
];

export const FALLBACK_PERSONAS: Persona[] = [
  {
    userId: 'maya_lin',
    name: 'Maya Lin',
    email: 'maya.lin@procureflow.enterprise',
    role: 'ML R&D Lead',
    departmentId: 'dept_ml_rnd',
    departmentName: 'ML Research & Dev',
    q4BudgetUsd: 25000,
    spentUsd: 6500,
    remainingUsd: 18500,
    utilizationPct: 26.0,
    intentMandate: {
      mandateId: 'im_maya_lin_q4',
      maxAutoUsd: 500,
      allowedTiers: ['PREFERRED', 'APPROVED'],
      maxBudgetUtilization: 0.8,
      status: 'ACTIVE',
    },
  },
  {
    userId: 'alex_rivera',
    name: 'Alex Rivera',
    email: 'alex.rivera@procureflow.enterprise',
    role: 'IT Ops Manager',
    departmentId: 'dept_it_ops',
    departmentName: 'IT Operations',
    q4BudgetUsd: 10000,
    spentUsd: 7850,
    remainingUsd: 2150,
    utilizationPct: 78.5,
    intentMandate: {
      mandateId: 'im_alex_rivera_q4',
      maxAutoUsd: 500,
      allowedTiers: ['PREFERRED', 'APPROVED'],
      maxBudgetUtilization: 0.8,
      status: 'ACTIVE',
    },
  },
  {
    userId: 'sam_chen',
    name: 'Sam Chen',
    email: 'sam.chen@procureflow.enterprise',
    role: 'Security Engineer',
    departmentId: 'dept_sec',
    departmentName: 'Information Security',
    q4BudgetUsd: 12000,
    spentUsd: 2400,
    remainingUsd: 9600,
    utilizationPct: 20.0,
    intentMandate: {
      mandateId: 'im_sam_chen_q4',
      maxAutoUsd: 500,
      allowedTiers: ['PREFERRED', 'APPROVED'],
      maxBudgetUtilization: 0.8,
      status: 'ACTIVE',
    },
  },
];

export const DEFAULT_AGENT_ROSTER: AgentTelemetryBreakdown[] = [
  {
    agentName: 'concierge_orchestrator',
    displayName: 'Concierge Orchestrator',
    modelName: 'gemini-3.5-flash',
    port: 8000,
    promptTokens: 0,
    outputTokens: 0,
    cachedTokens: 0,
    totalTokens: 0,
    estimatedCostUsd: 0,
    avgLatencyMs: 0,
    invocations: 0,
  },
  {
    agentName: 'catalog_agent',
    displayName: 'Catalog & Sourcing SLM',
    modelName: 'gemini-3.5-flash-lite',
    port: 8001,
    promptTokens: 0,
    outputTokens: 0,
    cachedTokens: 0,
    totalTokens: 0,
    estimatedCostUsd: 0,
    avgLatencyMs: 0,
    invocations: 0,
  },
  {
    agentName: 'policy_risk_agent',
    displayName: 'Policy & FinOps SLM',
    modelName: 'gemini-3.5-flash-lite',
    port: 8002,
    promptTokens: 0,
    outputTokens: 0,
    cachedTokens: 0,
    totalTokens: 0,
    estimatedCostUsd: 0,
    avgLatencyMs: 0,
    invocations: 0,
  },
  {
    agentName: 'ap2_settlement_agent',
    displayName: 'AP2 Settlement SLM',
    modelName: 'gemini-3.5-flash',
    port: 8003,
    promptTokens: 0,
    outputTokens: 0,
    cachedTokens: 0,
    totalTokens: 0,
    estimatedCostUsd: 0,
    avgLatencyMs: 0,
    invocations: 0,
  },
];

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asString(value: unknown, fallback = ''): string {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return fallback;
}

function asNumber(value: unknown, fallback = 0): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return fallback;
}

function normalizeVendorTier(value: unknown): VendorTier {
  const upper = asString(value, 'APPROVED').toUpperCase();
  if (upper.includes('PREFER')) return 'PREFERRED';
  if (upper.includes('BLOCK') || upper.includes('SANCTION') || upper.includes('DENY')) {
    return 'BLOCKED';
  }
  if (upper.includes('UNVERIF') || upper.includes('UNKNOWN') || upper.includes('REVIEW')) {
    return 'UNVERIFIED';
  }
  return 'APPROVED';
}

/**
 * Produces deterministic sorted-key compact JSON matching Python's
 * `json.dumps(obj, sort_keys=True, separators=(",", ":"))` when an object is supplied.
 */
export function canonicalizeJsonValue(value: unknown): string {
  if (value === null || typeof value !== 'object') {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalizeJsonValue(item)).join(',')}]`;
  }
  const rec = value as Record<string, unknown>;
  const sortedKeys = Object.keys(rec).sort();
  const entries = sortedKeys
    .filter((k) => rec[k] !== undefined)
    .map((k) => `${JSON.stringify(k)}:${canonicalizeJsonValue(rec[k])}`);
  return `{${entries.join(',')}}`;
}

export function resolveAgentMetadata(agentRaw: string): {
  agentName: string;
  displayName: string;
  port: number;
  modelName: string;
} {
  const lower = agentRaw.toLowerCase();
  if (lower.includes('catalog')) {
    return {
      agentName: 'catalog_agent',
      displayName: 'Catalog SLM',
      port: 8001,
      modelName: 'gemini-3.5-flash-lite',
    };
  }
  if (lower.includes('policy') || lower.includes('risk') || lower.includes('finops')) {
    return {
      agentName: 'policy_risk_agent',
      displayName: 'Policy & FinOps SLM',
      port: 8002,
      modelName: 'gemini-3.5-flash-lite',
    };
  }
  if (lower.includes('settle') || lower.includes('ap2') || lower.includes('mandate')) {
    return {
      agentName: 'ap2_settlement_agent',
      displayName: 'AP2 Settlement SLM',
      port: 8003,
      modelName: 'gemini-3.5-flash',
    };
  }
  return {
    agentName: 'concierge_orchestrator',
    displayName: 'Concierge Orchestrator',
    port: 8000,
    modelName: 'gemini-3.5-flash',
  };
}

export function normalizePersona(rawInput: unknown): Persona {
  const raw = asRecord(rawInput);
  const budgetObj = asRecord(raw.budget || raw.department_budget);
  const q4BudgetUsd = asNumber(
    raw.budget_total_usd ??
      raw.q4_budget_usd ??
      raw.total_budget_usd ??
      raw.allocated_usd ??
      raw.budget_limit_usd ??
      budgetObj.budget_total_usd ??
      budgetObj.q4_budget_usd ??
      budgetObj.total_budget_usd ??
      budgetObj.allocated_usd,
    20000,
  );
  const spentUsd = asNumber(
    raw.spent_usd ??
      raw.current_spend_usd ??
      raw.used_budget_usd ??
      budgetObj.spent_usd ??
      budgetObj.current_spend_usd,
    0,
  );
  const remainingUsd = asNumber(
    raw.remaining_usd ??
      raw.available_usd ??
      budgetObj.remaining_usd,
    Math.max(0, q4BudgetUsd - spentUsd),
  );
  const rawUtil = asNumber(
    raw.utilization_pct ??
      raw.budget_utilization_pct ??
      budgetObj.utilization_pct,
    q4BudgetUsd > 0 ? (spentUsd / q4BudgetUsd) * 100 : 0,
  );
  // Normalize 0..1 ratio vs 0..100 percentage if backend emits ratio
  const utilizationPct =
    rawUtil > 0 && rawUtil <= 1 && spentUsd / Math.max(1, q4BudgetUsd) > 0.015
      ? rawUtil * 100
      : rawUtil;

  const intentObj = asRecord(raw.intent_mandate || raw.intentMandate);
  const allowedRaw = Array.isArray(intentObj.allowed_tiers ?? intentObj.allowedTiers)
    ? ((intentObj.allowed_tiers ?? intentObj.allowedTiers) as unknown[])
    : ['PREFERRED', 'APPROVED'];

  const userId = asString(raw.user_id ?? raw.userId ?? raw.id, 'maya_lin');
  return {
    userId,
    name: asString(raw.full_name ?? raw.name ?? raw.display_name, userId),
    email: asString(raw.email, `${userId}@procureflow.enterprise`),
    role: asString(raw.role ?? raw.title, 'Procurement Requester'),
    departmentId: asString(
      raw.department_id ?? raw.departmentId ?? budgetObj.department_id,
      'dept_ml_rnd',
    ),
    departmentName: asString(
      raw.department_name ??
        raw.department ??
        raw.departmentName ??
        budgetObj.department_name,
      'Engineering',
    ),
    q4BudgetUsd,
    spentUsd,
    remainingUsd,
    utilizationPct: Number(utilizationPct.toFixed(1)),
    intentMandate: {
      mandateId: asString(
        intentObj.intent_mandate_id ??
          intentObj.mandate_id ??
          intentObj.mandateId ??
          raw.intent_mandate_id,
        `im_${userId}_q4`,
      ),
      maxAutoUsd: asNumber(
        intentObj.max_auto_usd ?? intentObj.maxAutoUsd ?? raw.max_auto_usd,
        500,
      ),
      allowedTiers: allowedRaw.map((t) => normalizeVendorTier(t)),
      maxBudgetUtilization: asNumber(
        intentObj.max_budget_utilization ?? intentObj.maxBudgetUtilization,
        0.8,
      ),
      status:
        asString(intentObj.status, 'ACTIVE').toUpperCase() === 'REVOKED'
          ? 'REVOKED'
          : 'ACTIVE',
      publicKeyB64: asString(
        intentObj.public_key_b64 ?? intentObj.publicKeyB64 ?? raw.public_key_b64,
        '',
      ) || undefined,
    },
  };
}

export function normalizeSession(rawInput: unknown, fallbackUserId: string): ProcurementSession {
  const raw = asRecord(rawInput);
  const sessionId = asString(raw.session_id ?? raw.sessionId ?? raw.id);
  const now = new Date().toISOString();
  return {
    sessionId,
    userId: asString(raw.user_id ?? raw.userId, fallbackUserId),
    title: asString(
      raw.title ?? raw.summary ?? raw.last_user_message,
      `Session ${sessionId.slice(0, 8)}`,
    ),
    createdAt: asString(raw.created_at ?? raw.createdAt, now),
    updatedAt: asString(raw.updated_at ?? raw.updatedAt ?? raw.created_at, now),
    messageCount:
      raw.message_count !== undefined || raw.messageCount !== undefined
        ? asNumber(raw.message_count ?? raw.messageCount, 0)
        : undefined,
  };
}

function normalizeCartLineItem(itemInput: unknown, defaultVendor = ''): CartLineItem {
  const item = asRecord(itemInput);
  const quantity = Math.max(1, asNumber(item.quantity ?? item.qty, 1));
  const unitPriceUsd = asNumber(
    item.unit_price_usd ?? item.unitPriceUsd ?? item.unit_price ?? item.price_usd,
    0,
  );
  const discountPct = asNumber(
    item.discount_pct ?? item.volume_discount_pct ?? item.discountPct,
    0,
  );
  const effectiveUnit = asNumber(
    item.discounted_unit_price_usd ?? item.net_unit_price_usd,
    unitPriceUsd * (1 - discountPct / 100),
  );
  const lineTotalUsd = asNumber(
    item.line_total_usd ??
      item.total_usd ??
      item.subtotal_usd ??
      item.lineTotalUsd ??
      item.extended_price_usd,
    effectiveUnit * quantity,
  );

  return {
    sku: asString(item.sku ?? item.sku_id ?? item.product_code, 'SKU-ITEM'),
    name: asString(
      item.name ?? item.product_name ?? item.description ?? item.title,
      'Enterprise Item',
    ),
    vendorName: asString(item.vendor_name ?? item.vendor ?? item.vendorName, defaultVendor),
    vendorTier: item.vendor_tier || item.vendorTier
      ? normalizeVendorTier(item.vendor_tier ?? item.vendorTier)
      : undefined,
    quantity,
    unitPriceUsd,
    discountPct,
    lineTotalUsd,
    leadTimeDays:
      item.lead_time_days !== undefined || item.leadTimeDays !== undefined
        ? asNumber(item.lead_time_days ?? item.leadTimeDays, 3)
        : undefined,
  };
}

export function normalizeA2UISurface(rawInput: unknown, indexHint = 0): A2UISurface | null {
  const rawRecord = asRecord(rawInput);
  const outer =
    rawRecord.surface && typeof rawRecord.surface === 'object' && !Array.isArray(rawRecord.surface)
      ? asRecord(rawRecord.surface)
      : rawRecord;
  const rawType = asString(
    outer.surface_type ?? outer.surfaceType ?? outer.widget_type ?? outer.component ?? outer.type,
  );

  let surfaceType: A2UISurfaceType | null = null;
  if (
    rawType === 'VendorComparisonCard' ||
    rawType.toLowerCase().includes('vendorcomparison')
  ) {
    surfaceType = 'VendorComparisonCard';
  } else if (
    rawType === 'BudgetGaugeCard' ||
    rawType.toLowerCase().includes('budgetgauge')
  ) {
    surfaceType = 'BudgetGaugeCard';
  } else if (
    rawType === 'AP2CartMandateApprovalCard' ||
    rawType.toLowerCase().includes('cartmandate') ||
    rawType.toLowerCase().includes('mandateapproval')
  ) {
    surfaceType = 'AP2CartMandateApprovalCard';
  } else if (
    rawType === 'OrderReceiptCard' ||
    rawType.toLowerCase().includes('orderreceipt') ||
    rawType.toLowerCase().includes('receiptcard')
  ) {
    surfaceType = 'OrderReceiptCard';
  }

  if (!surfaceType) {
    return null;
  }

  const payloadCandidate =
    outer.payload ?? outer.data ?? outer.props ?? outer.content ?? outer;
  const data = asRecord(payloadCandidate);
  const surfaceId = asString(
    outer.surface_id ??
      outer.id ??
      data.cart_mandate_id ??
      data.order_id ??
      `${surfaceType}-${Date.now()}-${indexHint}`,
  );

  if (surfaceType === 'VendorComparisonCard') {
    const rawOptions = Array.isArray(data.options)
      ? data.options
      : Array.isArray(data.vendors)
        ? data.vendors
        : Array.isArray(data.quotes)
          ? data.quotes
          : Array.isArray(data.items)
            ? data.items
            : [];

    const options: VendorComparisonOption[] = rawOptions.map((optRaw, idx) => {
      const opt = asRecord(optRaw);
      const quantity = Math.max(
        1,
        asNumber(opt.quantity ?? opt.qty ?? data.requested_quantity, 1),
      );
      const unitPriceUsd = asNumber(
        opt.unit_price_usd ?? opt.unitPriceUsd ?? opt.unit_price ?? opt.base_price_usd,
        0,
      );
      const volumeDiscountPct = asNumber(
        opt.volume_discount_pct ??
          opt.discount_pct ??
          opt.volumeDiscountPct ??
          opt.bulk_discount_pct,
        0,
      );
      const discountedUnitPriceUsd = asNumber(
        opt.discounted_unit_price_usd ??
          opt.net_unit_price_usd ??
          opt.effective_unit_price_usd,
        unitPriceUsd * (1 - volumeDiscountPct / 100),
      );
      const totalPriceUsd = asNumber(
        opt.total_price_usd ??
          opt.total_usd ??
          opt.subtotal_usd ??
          opt.extended_price_usd ??
          opt.totalPriceUsd,
        discountedUnitPriceUsd * quantity,
      );
      const leadTimeDays = asNumber(
        opt.lead_time_days ?? opt.leadTimeDays ?? opt.delivery_days,
        3,
      );
      const requestedSla = asNumber(data.requested_sla_days ?? data.sla_days, 0);
      const meetsSla =
        typeof opt.meets_sla === 'boolean'
          ? opt.meets_sla
          : typeof opt.meetsSla === 'boolean'
            ? opt.meetsSla
            : requestedSla > 0
              ? leadTimeDays <= requestedSla
              : leadTimeDays <= 5;

      const tier = normalizeVendorTier(
        opt.vendor_tier ?? opt.compliance_tier ?? opt.tier ?? opt.vendorTier,
      );

      return {
        optionId: asString(opt.option_id ?? opt.sku_id ?? opt.sku ?? `opt-${idx}`),
        sku: asString(opt.sku_id ?? opt.sku, `SKU-${idx + 1}`),
        name: asString(
          opt.name ?? opt.product_name ?? opt.title ?? data.product_name,
          'Catalog Item',
        ),
        vendorId: asString(opt.vendor_id ?? opt.vendorId) || undefined,
        vendorName: asString(opt.vendor_name ?? opt.vendor ?? opt.vendorName, 'Enterprise Vendor'),
        vendorTier: tier,
        unitPriceUsd,
        quantity,
        volumeDiscountPct,
        discountedUnitPriceUsd,
        totalPriceUsd,
        leadTimeDays,
        meetsSla,
        slaLabel: asString(
          opt.sla_label ?? opt.lead_time_label,
          `${leadTimeDays}d Lead Time`,
        ),
        stockStatus: asString(opt.stock_status ?? opt.availability, 'In Stock'),
        recommended: Boolean(
          opt.recommended ??
            opt.is_recommended ??
            (idx === 0 && tier !== 'BLOCKED'),
        ),
        notes: asString(opt.specs ?? opt.notes ?? opt.reason) || undefined,
      };
    });

    const normalized: VendorComparisonData = {
      title: asString(
        outer.title ?? data.title ?? data.heading,
        'Sourcing & Vendor Quote Comparison',
      ),
      querySummary:
        asString(data.notes ?? data.query_summary ?? data.subtitle ?? data.summary) ||
        undefined,
      requestedQuantity:
        data.requested_quantity !== undefined
          ? asNumber(data.requested_quantity)
          : undefined,
      requestedSlaDays:
        data.requested_sla_days !== undefined
          ? asNumber(data.requested_sla_days)
          : undefined,
      options,
    };

    return {
      id: surfaceId,
      surfaceType: 'VendorComparisonCard',
      data: normalized,
    };
  }

  if (surfaceType === 'BudgetGaugeCard') {
    const totalBudgetUsd = Math.max(
      1,
      asNumber(
        data.budget_total_usd ??
          data.total_budget_usd ??
          data.q4_budget_usd ??
          data.allocated_usd ??
          data.budget_limit_usd ??
          data.totalBudgetUsd,
        20000,
      ),
    );
    const currentSpendUsd = asNumber(
      data.current_spent_usd ??
        data.current_spend_usd ??
        data.spent_usd ??
        data.currentSpendUsd,
      0,
    );
    const orderAmountUsd = asNumber(
      data.order_amount_usd ??
        data.proposed_order_usd ??
        data.cart_total_usd ??
        data.delta_usd ??
        data.orderAmountUsd,
      0,
    );
    const projectedSpendUsd = asNumber(
      data.projected_spent_usd ??
        data.projected_spend_usd ??
        data.post_order_spend_usd ??
        data.projectedSpendUsd,
      currentSpendUsd + orderAmountUsd,
    );
    const remainingPostOrderUsd = asNumber(
      data.remaining_post_order_usd ??
        data.remaining_after_usd ??
        data.remaining_usd,
      Math.max(0, totalBudgetUsd - projectedSpendUsd),
    );

    const rawCurrPct = asNumber(
      data.current_utilization_pct ?? data.currentUtilizationPct,
      (currentSpendUsd / totalBudgetUsd) * 100,
    );
    const currentUtilizationPct =
      rawCurrPct > 0 && rawCurrPct <= 1 && currentSpendUsd / totalBudgetUsd > 0.015
        ? rawCurrPct * 100
        : rawCurrPct;

    const rawProjPct = asNumber(
      data.projected_utilization_pct ??
        data.post_order_utilization_pct ??
        data.projectedUtilizationPct,
      (projectedSpendUsd / totalBudgetUsd) * 100,
    );
    const projectedUtilizationPct =
      rawProjPct > 0 && rawProjPct <= 1 && projectedSpendUsd / totalBudgetUsd > 0.015
        ? rawProjPct * 100
        : rawProjPct;

    const warningThresholdPct = asNumber(
      data.warning_threshold_pct ?? data.hitl_threshold_pct,
      80,
    );
    const hardCapPct = asNumber(data.hard_cap_pct, 100);

    let policyVerdict: BudgetGaugeData['policyVerdict'] = 'AUTO_OK';
    const rawVerdict = asString(data.policy_verdict ?? data.status ?? data.verdict).toUpperCase();
    if (
      rawVerdict.includes('BLOCK') ||
      rawVerdict.includes('EXCEED') ||
      projectedUtilizationPct > hardCapPct
    ) {
      policyVerdict = 'HARD_BLOCKED';
    } else if (
      rawVerdict.includes('WARN') ||
      rawVerdict.includes('HITL') ||
      rawVerdict.includes('ESCALAT') ||
      projectedUtilizationPct >= warningThresholdPct
    ) {
      policyVerdict = 'HITL_WARNING';
    }

    return {
      id: surfaceId,
      surfaceType: 'BudgetGaugeCard',
      data: {
        departmentId: asString(data.department_id ?? data.departmentId, 'dept'),
        departmentName: asString(
          data.department_name ?? data.department ?? data.departmentName,
          'Department',
        ),
        fiscalPeriod: asString(data.fiscal_period ?? data.quarter, 'Q4 FY26'),
        totalBudgetUsd,
        currentSpendUsd,
        orderAmountUsd,
        projectedSpendUsd,
        remainingPostOrderUsd,
        currentUtilizationPct: Number(currentUtilizationPct.toFixed(1)),
        projectedUtilizationPct: Number(projectedUtilizationPct.toFixed(1)),
        warningThresholdPct,
        hardCapPct,
        policyVerdict,
        notes: asString(data.notes ?? data.summary ?? data.message) || undefined,
      },
    };
  }

  if (surfaceType === 'AP2CartMandateApprovalCard') {
    const vendorName = asString(
      data.vendor_name ?? data.vendor ?? data.merchant_name ?? data.vendorName,
      'Enterprise Supplier',
    );
    const rawItems = Array.isArray(data.line_items)
      ? data.line_items
      : Array.isArray(data.items)
        ? data.items
        : Array.isArray(data.cart_items)
          ? data.cart_items
          : [];
    const lineItems = rawItems.map((it) => normalizeCartLineItem(it, vendorName));
    const computedTotal = lineItems.reduce((acc, it) => acc + it.lineTotalUsd, 0);
    const totalUsd = asNumber(
      data.total_usd ?? data.amount_usd ?? data.totalUsd ?? data.grand_total_usd,
      computedTotal,
    );
    const subtotalUsd = asNumber(
      data.subtotal_usd ?? data.subtotalUsd,
      lineItems.reduce((acc, it) => acc + it.unitPriceUsd * it.quantity, 0) || totalUsd,
    );
    const discountTotalUsd = asNumber(
      data.discount_total_usd ?? data.discount_usd,
      Math.max(0, subtotalUsd - totalUsd),
    );

    const rawReasons = Array.isArray(data.escalation_reasons)
      ? data.escalation_reasons
      : Array.isArray(data.escalation_triggers)
        ? data.escalation_triggers
        : Array.isArray(data.triggers)
          ? data.triggers
          : Array.isArray(data.policy_warnings)
            ? data.policy_warnings
            : data.reason
              ? [data.reason]
              : [];
    const escalationReasons = rawReasons.map((r) => asString(r)).filter(Boolean);
    if (escalationReasons.length === 0) {
      if (totalUsd > 500) {
        escalationReasons.push('Order exceeds $500.00 IntentMandate autonomous threshold');
      } else {
        escalationReasons.push('Policy escalation triggered human CartMandate verification');
      }
    }

    const cartMandateId = asString(
      data.cart_mandate_id ?? data.mandate_id ?? data.cartMandateId ?? data.id,
      `CART-${Date.now()}`,
    );

    const rawCanonical =
      data.canonical_payload_to_sign ??
      data.canonical_payload ??
      data.canonicalPayload ??
      data.canonical_json ??
      data.mandate_payload;
    const canonicalPayload =
      typeof rawCanonical === 'string' && rawCanonical.length > 0
        ? rawCanonical
        : canonicalizeJsonValue(
            rawCanonical && typeof rawCanonical === 'object'
              ? rawCanonical
              : {
                  cart_mandate_id: cartMandateId,
                  cart_sha256: asString(data.cart_sha256 ?? data.cartSha256),
                  currency: asString(data.currency, 'USD'),
                  department_id: asString(data.department_id),
                  total_usd: totalUsd,
                  user_id: asString(data.user_id),
                  vendor_id: asString(data.vendor_id),
                },
          );

    const rawStatus = asString(data.status, 'PENDING_SIGNATURE').toUpperCase();
    let status: AP2CartMandateApprovalData['status'] = 'PENDING_SIGNATURE';
    if (rawStatus.includes('SIGN') || rawStatus.includes('APPROV') || rawStatus.includes('SETTL')) {
      status = 'SIGNED_AND_SETTLED';
    } else if (rawStatus.includes('REJECT') || rawStatus.includes('DEN')) {
      status = 'REJECTED';
    } else if (rawStatus.includes('EXPIR')) {
      status = 'EXPIRED';
    }

    return {
      id: cartMandateId,
      surfaceType: 'AP2CartMandateApprovalCard',
      data: {
        cartMandateId,
        intentMandateId:
          asString(data.intent_mandate_id ?? data.intentMandateId) || undefined,
        departmentName: asString(
          data.department_name ??
            data.department ??
            data.department_id ??
            data.departmentName,
          'Engineering',
        ),
        vendorName,
        vendorTier: normalizeVendorTier(data.vendor_tier ?? data.vendorTier),
        lineItems,
        subtotalUsd,
        discountTotalUsd,
        totalUsd,
        currency: asString(data.currency, 'USD'),
        escalationReasons,
        cartSha256: asString(
          data.cart_sha256 ?? data.cartSha256 ?? data.digest_sha256 ?? data.sha256,
          'pending-sha256-digest',
        ),
        canonicalPayload,
        status,
        signatureB64: asString(data.signature_b64 ?? data.signatureB64) || undefined,
        signerPublicKeyB64:
          asString(data.public_key_b64 ?? data.signerPublicKeyB64) || undefined,
        expiresAt: asString(data.expires_at ?? data.expiresAt) || undefined,
      },
    };
  }

  // OrderReceiptCard
  const vendorName = asString(
    data.vendor_name ?? data.vendor ?? data.vendorName,
    'Approved Supplier',
  );
  const rawItems = Array.isArray(data.line_items)
    ? data.line_items
    : Array.isArray(data.items)
      ? data.items
      : [];
  const lineItems = rawItems.map((it) => normalizeCartLineItem(it, vendorName));
  const totalUsd = asNumber(
    data.total_usd ?? data.amount_usd ?? data.totalUsd,
    lineItems.reduce((acc, it) => acc + it.lineTotalUsd, 0),
  );
  const rawAuthPath = asString(
    data.authorization_mode ??
      data.authorization_path ??
      data.auth_path ??
      data.settlement_mode ??
      data.authorizationPath,
    data.cart_mandate_id ? 'HITL_CART_MANDATE' : 'AUTO_INTENT_MANDATE',
  ).toUpperCase();
  const authorizationPath: OrderReceiptData['authorizationPath'] =
    rawAuthPath.includes('HITL') || rawAuthPath.includes('CART')
      ? 'HITL_CART_MANDATE'
      : 'AUTO_INTENT_MANDATE';

  const estimatedDeliveryDays = Math.max(
    1,
    asNumber(data.estimated_delivery_days ?? data.lead_time_days ?? data.delivery_days, 3),
  );

  const rawSteps = Array.isArray(data.delivery_timeline ?? data.delivery_steps)
    ? ((data.delivery_timeline ?? data.delivery_steps) as unknown[])
    : [];
  const deliverySteps: DeliveryTimelineStep[] =
    rawSteps.length > 0
      ? rawSteps.map((s) => {
          const sr = asRecord(s);
          const st = asString(sr.status, 'upcoming').toLowerCase();
          return {
            label: asString(sr.label ?? sr.step ?? sr.title, 'Fulfillment Step'),
            detail: asString(sr.detail ?? sr.description ?? sr.eta, ''),
            status:
              st === 'completed' || st === 'done'
                ? 'completed'
                : st === 'current' || st === 'active' || st === 'in_progress'
                  ? 'current'
                  : 'upcoming',
          };
        })
      : [
          {
            label: 'AP2 Mandate Verified',
            detail:
              authorizationPath === 'HITL_CART_MANDATE'
                ? 'Ed25519 CartMandate + PaymentMandate verified'
                : 'Standing IntentMandate + PaymentMandate verified',
            status: 'completed',
          },
          {
            label: 'Ledger Committed & PO Dispatched',
            detail: `Transmitted to ${vendorName}`,
            status: 'completed',
          },
          {
            label: 'Warehouse Allocation',
            detail: 'Preparing shipment & serial tracking',
            status: 'current',
          },
          {
            label: `Estimated Delivery (${estimatedDeliveryDays} business days)`,
            detail: asString(
              data.estimated_delivery_date,
              `Within ${estimatedDeliveryDays} business days`,
            ),
            status: 'upcoming',
          },
        ];

  const orderId = asString(
    data.order_id ?? data.orderId ?? data.receipt_id ?? data.id,
    `ORD-${Date.now()}`,
  );

  return {
    id: orderId,
    surfaceType: 'OrderReceiptCard',
    data: {
      orderId,
      status: asString(data.status, 'SETTLED').toUpperCase(),
      authorizationPath,
      intentMandateId: asString(
        data.intent_mandate_id ?? data.intentMandateId,
        'INT-AUTO-VERIFIED',
      ),
      cartMandateId:
        asString(data.cart_mandate_id ?? data.cartMandateId) || undefined,
      paymentMandateId: asString(
        data.payment_mandate_id ?? data.paymentMandateId,
        `PAY-${orderId.replace(/^ORD-/, '')}`,
      ),
      vendorName,
      vendorTier: normalizeVendorTier(data.vendor_tier ?? data.vendorTier),
      departmentName: asString(
        data.department_name ?? data.department ?? data.departmentName,
        'Department',
      ),
      lineItems,
      totalUsd,
      currency: asString(data.currency, 'USD'),
      signatureB64: asString(
        data.signature_preview ??
          data.signature_b64 ??
          data.payment_signature_b64 ??
          data.cart_signature_b64 ??
          data.signatureB64,
        'Ed25519VerifiedSignature',
      ),
      publicKeyB64:
        asString(data.public_key_b64 ?? data.publicKeyB64) || undefined,
      cartSha256: asString(data.cart_sha256 ?? data.cartSha256) || undefined,
      estimatedDeliveryDays,
      estimatedDeliveryDate:
        asString(data.estimated_delivery_date ?? data.estimatedDeliveryDate) ||
        undefined,
      settledAt: asString(
        data.settled_at ?? data.created_at ?? data.timestamp,
        new Date().toISOString(),
      ),
      deliverySteps,
    },
  };
}

export function normalizeChatMessage(rawInput: unknown, idx = 0): ChatMessage {
  const raw = asRecord(rawInput);
  const roleRaw = asString(raw.role ?? raw.author, 'assistant').toLowerCase();
  const role: ChatMessage['role'] =
    roleRaw === 'user' ? 'user' : roleRaw === 'system' ? 'system' : 'assistant';

  const rawSurfaces = Array.isArray(raw.a2ui_surfaces)
    ? raw.a2ui_surfaces
    : Array.isArray(raw.surfaces)
      ? raw.surfaces
      : Array.isArray(raw.widgets)
        ? raw.widgets
        : [];
  const surfaces = rawSurfaces
    .map((s, sIdx) => normalizeA2UISurface(s, sIdx))
    .filter((s): s is A2UISurface => s !== null);

  const rawSkills = Array.isArray(raw.skills_activated)
    ? raw.skills_activated
    : Array.isArray(raw.skillsActivated)
      ? raw.skillsActivated
      : [];

  return {
    id: asString(raw.id ?? raw.message_id, `msg-${idx}-${Date.now()}`),
    role,
    content: asString(raw.content ?? raw.text ?? raw.message, ''),
    createdAt: asString(
      raw.created_at ?? raw.timestamp ?? raw.createdAt,
      new Date().toISOString(),
    ),
    agentActivities: [],
    skillsActivated: rawSkills.map((sk, kIdx) => {
      if (typeof sk === 'string') {
        return {
          id: `sk-${idx}-${kIdx}`,
          skillName: sk,
          agentName: 'concierge_orchestrator',
          level: 'L2',
          timestamp: new Date().toISOString(),
        };
      }
      const skr = asRecord(sk);
      return {
        id: asString(skr.id, `sk-${idx}-${kIdx}`),
        skillName: asString(skr.skill_name ?? skr.name, 'skill'),
        agentName: asString(skr.agent ?? skr.agent_name, 'concierge_orchestrator'),
        level: asString(skr.level, 'L2'),
        timestamp: asString(skr.timestamp, new Date().toISOString()),
      };
    }),
    surfaces,
    hitlRequired: Boolean(raw.hitl_required ?? raw.hitlRequired),
  };
}

export function normalizeTelemetrySummary(rawInput: unknown): TelemetrySummary {
  const raw = asRecord(rawInput);
  const totalsObj = asRecord(raw.totals ?? raw.kpis ?? raw);

  const promptTokens = asNumber(
    totalsObj.prompt_tokens ??
      totalsObj.prompt_token_count ??
      totalsObj.input_tokens ??
      totalsObj.promptTokens,
    0,
  );
  const outputTokens = asNumber(
    totalsObj.output_tokens ??
      totalsObj.candidates_tokens ??
      totalsObj.candidates_token_count ??
      totalsObj.completion_tokens ??
      totalsObj.outputTokens,
    0,
  );
  const cachedTokens = asNumber(
    totalsObj.cached_tokens ??
      totalsObj.cached_content_token_count ??
      totalsObj.cachedTokens,
    0,
  );
  const totalTokens = asNumber(
    totalsObj.total_tokens ??
      totalsObj.total_token_count ??
      totalsObj.totalTokens,
    promptTokens + outputTokens,
  );
  const estimatedCostUsd = asNumber(
    totalsObj.estimated_cost_usd ??
      totalsObj.total_cost_usd ??
      totalsObj.cost_usd ??
      totalsObj.estimatedCostUsd,
    0,
  );
  const a2aHops = asNumber(
    totalsObj.a2a_calls_count ??
      totalsObj.a2a_hops ??
      totalsObj.a2a_call_count ??
      totalsObj.remote_a2a_calls ??
      totalsObj.a2aHops,
    0,
  );

  const rawSkills = Array.isArray(raw.activated_skills)
    ? raw.activated_skills
    : Array.isArray(raw.active_skills)
      ? raw.active_skills
      : Array.isArray(raw.skills_activated)
        ? raw.skills_activated
        : Array.isArray(raw.skills)
          ? raw.skills
          : [];
  const activeSkills = Array.from(
    new Set(
      rawSkills
        .map((s) =>
          typeof s === 'string'
            ? s
            : asString(asRecord(s).skill_name ?? asRecord(s).name),
        )
        .filter(Boolean),
    ),
  );

  const rawAgents =
    raw.per_agent ?? raw.agents ?? raw.by_agent ?? raw.agent_breakdown ?? [];
  const agentMap = new Map<string, AgentTelemetryBreakdown>();
  for (const base of DEFAULT_AGENT_ROSTER) {
    agentMap.set(base.agentName, { ...base });
  }

  if (Array.isArray(rawAgents)) {
    for (const item of rawAgents) {
      const ar = asRecord(item);
      const meta = resolveAgentMetadata(
        asString(ar.agent_name ?? ar.agent ?? ar.service_name ?? ar.agentName),
      );
      const pTok = asNumber(ar.prompt_tokens ?? ar.prompt_token_count ?? ar.input_tokens, 0);
      const oTok = asNumber(
        ar.output_tokens ??
          ar.candidates_tokens ??
          ar.candidates_token_count ??
          ar.completion_tokens,
        0,
      );
      const cTok = asNumber(ar.cached_tokens ?? ar.cached_content_token_count, 0);
      const tTok = asNumber(ar.total_tokens ?? ar.total_token_count, pTok + oTok);
      agentMap.set(meta.agentName, {
        agentName: meta.agentName,
        displayName: asString(ar.display_name, meta.displayName),
        modelName: asString(ar.model_name ?? ar.model, meta.modelName),
        port: asNumber(ar.port, meta.port),
        promptTokens: pTok,
        outputTokens: oTok,
        cachedTokens: cTok,
        totalTokens: tTok,
        estimatedCostUsd: asNumber(
          ar.estimated_cost_usd ?? ar.cost_usd ?? ar.total_cost_usd,
          0,
        ),
        avgLatencyMs: asNumber(
          ar.avg_latency_ms ?? ar.latency_ms ?? ar.duration_ms,
          0,
        ),
        invocations: asNumber(
          ar.llm_calls ?? ar.invocations ?? ar.calls ?? ar.turn_count,
          tTok > 0 ? 1 : 0,
        ),
      });
    }
  } else if (rawAgents && typeof rawAgents === 'object') {
    for (const [key, val] of Object.entries(rawAgents as Record<string, unknown>)) {
      const ar = asRecord(val);
      const meta = resolveAgentMetadata(key);
      const pTok = asNumber(ar.prompt_tokens ?? ar.input_tokens, 0);
      const oTok = asNumber(ar.output_tokens ?? ar.candidates_tokens, 0);
      const cTok = asNumber(ar.cached_tokens, 0);
      const tTok = asNumber(ar.total_tokens, pTok + oTok);
      agentMap.set(meta.agentName, {
        agentName: meta.agentName,
        displayName: meta.displayName,
        modelName: asString(ar.model_name ?? ar.model, meta.modelName),
        port: asNumber(ar.port, meta.port),
        promptTokens: pTok,
        outputTokens: oTok,
        cachedTokens: cTok,
        totalTokens: tTok,
        estimatedCostUsd: asNumber(ar.estimated_cost_usd ?? ar.cost_usd, 0),
        avgLatencyMs: asNumber(ar.avg_latency_ms ?? ar.latency_ms, 0),
        invocations: asNumber(ar.invocations ?? ar.calls, tTok > 0 ? 1 : 0),
      });
    }
  }

  return {
    totalTokens,
    promptTokens,
    outputTokens,
    cachedTokens,
    estimatedCostUsd,
    a2aHops,
    activeSkills,
    perAgent: Array.from(agentMap.values()),
    updatedAt: new Date().toISOString(),
  };
}

function parseTimestampMs(val: unknown, fallback: number): number {
  if (typeof val === 'number' && Number.isFinite(val)) {
    // Handle nanoseconds, microseconds, seconds, or milliseconds
    if (val > 1e15) return val / 1e6;
    if (val > 1e12) return val;
    if (val > 1e9) return val * 1000;
    return val;
  }
  if (typeof val === 'string') {
    const num = Number(val);
    if (Number.isFinite(num) && val.trim() !== '') {
      return parseTimestampMs(num, fallback);
    }
    const parsedDate = Date.parse(val);
    if (!Number.isNaN(parsedDate)) {
      return parsedDate;
    }
  }
  return fallback;
}

export function normalizeTelemetrySpan(rawInput: unknown, idx = 0): TelemetrySpan {
  const raw = asRecord(rawInput);
  let attrs: Record<string, unknown> = {};
  if (typeof raw.attributes === 'string') {
    try {
      attrs = asRecord(JSON.parse(raw.attributes));
    } catch {
      attrs = { raw_attributes: raw.attributes };
    }
  } else if (raw.attributes && typeof raw.attributes === 'object') {
    attrs = asRecord(raw.attributes);
  }

  const serviceHint = asString(
    raw.service_name ??
      raw.agent_name ??
      attrs['service.name'] ??
      attrs['agent.name'] ??
      raw.name,
  );
  const meta = resolveAgentMetadata(serviceHint);

  const nowMs = Date.now();
  const durationMs = Math.max(
    0.5,
    asNumber(
      raw.duration_ms ?? raw.latency_ms ?? attrs['latency_ms'] ?? raw.durationMs,
      12,
    ),
  );
  const startTimeMs = parseTimestampMs(
    raw.start_time_ms ?? raw.start_time ?? raw.started_at ?? raw.startTimeMs,
    nowMs - durationMs + idx * 5,
  );
  const endTimeMs = parseTimestampMs(
    raw.end_time_ms ?? raw.end_time ?? raw.ended_at ?? raw.endTimeMs,
    startTimeMs + durationMs,
  );

  const rawStatus = asString(raw.status ?? raw.status_code, 'OK').toUpperCase();
  const status: TelemetrySpan['status'] = rawStatus.includes('ERR')
    ? 'ERROR'
    : rawStatus.includes('UNSET')
      ? 'UNSET'
      : 'OK';

  return {
    spanId: asString(raw.span_id ?? raw.spanId ?? raw.id, `span-${idx}`),
    traceId: asString(raw.trace_id ?? raw.traceId, 'local-trace'),
    parentSpanId:
      asString(raw.parent_span_id ?? raw.parentSpanId) || null,
    name: asString(raw.name ?? raw.span_name ?? raw.operation, 'agent.operation'),
    serviceName: asString(raw.service_name ?? attrs['service.name'], meta.agentName),
    agentName: meta.agentName,
    port: asNumber(raw.port ?? attrs['server.port'], meta.port),
    kind: asString(raw.kind ?? raw.span_kind, 'INTERNAL'),
    startTimeMs,
    endTimeMs: Math.max(startTimeMs + 0.5, endTimeMs),
    durationMs: Number(durationMs.toFixed(1)),
    status,
    attributes: attrs,
  };
}

/**
 * Fetches available employee personas from `GET /api/personas` with automatic offline fallback.
 */
export async function fetchPersonas(): Promise<{
  personas: Persona[];
  backendOnline: boolean;
}> {
  try {
    const res = await fetch('/api/personas', {
      headers: { Accept: 'application/json' },
    });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    const json = (await res.json()) as unknown;
    const list = Array.isArray(json)
      ? json
      : Array.isArray(asRecord(json).personas)
        ? (asRecord(json).personas as unknown[])
        : [];
    if (list.length === 0) {
      return { personas: FALLBACK_PERSONAS, backendOnline: true };
    }
    return {
      personas: list.map((item) => normalizePersona(item)),
      backendOnline: true,
    };
  } catch {
    return {
      personas: FALLBACK_PERSONAS,
      backendOnline: false,
    };
  }
}

/**
 * Registers the browser-generated 32-byte raw Ed25519 public key (`public_key_b64`)
 * for the active persona with `POST /api/personas/${userId}/register-key`.
 */
export async function registerPersonaPublicKey(
  userId: string,
  publicKeyB64: string,
): Promise<boolean> {
  try {
    const res = await fetch(`/api/personas/${encodeURIComponent(userId)}/register-key`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({
        public_key_b64: publicKeyB64,
      }),
    });
    return res.ok;
  } catch {
    return false;
  }
}

/**
 * Resets the demo domain & budget ledger via `POST /api/demo/reset`.
 */
export async function resetDemoLedger(): Promise<void> {
  const res = await fetch('/api/demo/reset', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({}),
  });
  if (!res.ok) {
    throw new Error(`Failed to reset demo ledger (HTTP ${res.status})`);
  }
}

/**
 * Lists persistent SQLite procurement sessions for a persona (`GET /api/sessions?user_id=...`).
 */
export async function fetchSessions(userId: string): Promise<ProcurementSession[]> {
  try {
    const res = await fetch(`/api/sessions?user_id=${encodeURIComponent(userId)}`, {
      headers: { Accept: 'application/json' },
    });
    if (!res.ok) return [];
    const json = (await res.json()) as unknown;
    const rawList = Array.isArray(json)
      ? json
      : Array.isArray(asRecord(json).sessions)
        ? (asRecord(json).sessions as unknown[])
        : [];
    return rawList
      .map((item) => normalizeSession(item, userId))
      .filter((s) => Boolean(s.sessionId));
  } catch {
    return [];
  }
}

/**
 * Creates a new procurement session (`POST /api/sessions`).
 */
export async function createSession(
  userId: string,
  title?: string,
): Promise<ProcurementSession> {
  try {
    const res = await fetch('/api/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({
        user_id: userId,
        ...(title ? { title } : {}),
      }),
    });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    const json = await res.json();
    const normalized = normalizeSession(json, userId);
    if (normalized.sessionId) {
      return normalized;
    }
  } catch {
    // Fallback local session identifier if backend is offline; backend will create on stream when booted
  }

  const fallbackId = `sess-${crypto.randomUUID().slice(0, 8)}`;
  const now = new Date().toISOString();
  return {
    sessionId: fallbackId,
    userId,
    title: title || 'New Procurement Session',
    createdAt: now,
    updatedAt: now,
  };
}

/**
 * Loads historical messages and A2UI surfaces for a session (`GET /api/sessions/{session_id}/messages`).
 */
export async function fetchSessionMessages(sessionId: string): Promise<ChatMessage[]> {
  try {
    const res = await fetch(
      `/api/sessions/${encodeURIComponent(sessionId)}/messages`,
      {
        headers: { Accept: 'application/json' },
      },
    );
    if (!res.ok) return [];
    const json = (await res.json()) as unknown;
    const rawList = Array.isArray(json)
      ? json
      : Array.isArray(asRecord(json).messages)
        ? (asRecord(json).messages as unknown[])
        : [];
    return rawList.map((m, idx) => normalizeChatMessage(m, idx));
  } catch {
    return [];
  }
}

/**
 * Streams a conversational turn from `POST /api/chat/stream`.
 */
export async function streamChatTurn(params: {
  userId: string;
  sessionId: string;
  message: string;
  signal?: AbortSignal;
  onEvent: (event: ParsedSSEEvent) => void;
}): Promise<void> {
  await postSSEStream({
    url: '/api/chat/stream',
    body: {
      user_id: params.userId,
      session_id: params.sessionId,
      message: params.message,
    },
    signal: params.signal,
    onEvent: params.onEvent,
  });
}

export interface MandateDecisionResult {
  approved: boolean;
  message: string;
  surfaces: A2UISurface[];
}

/**
 * Submits human cryptographic CartMandate approval or rejection to
 * `POST /api/ap2/mandates/{cart_mandate_id}/decision`.
 * Supports both JSON and SSE responses from the Gateway.
 */
export async function submitMandateDecision(params: {
  cartMandateId: string;
  userId: string;
  sessionId: string;
  decision: 'approve' | 'reject';
  signatureB64?: string;
  publicKeyB64?: string;
  canonicalPayload?: string;
  reason?: string;
}): Promise<MandateDecisionResult> {
  const body: Record<string, unknown> = {
    user_id: params.userId,
    session_id: params.sessionId,
    decision: params.decision,
    approved: params.decision === 'approve',
  };
  if (params.signatureB64) {
    body.signature_b64 = params.signatureB64;
  }
  if (params.publicKeyB64) {
    body.public_key_b64 = params.publicKeyB64;
  }
  if (params.canonicalPayload) {
    body.canonical_payload = params.canonicalPayload;
  }
  if (params.reason) {
    body.reason = params.reason;
  }

  const surfaces: A2UISurface[] = [];
  let textChunks = '';

  const response = await fetch(
    `/api/ap2/mandates/${encodeURIComponent(params.cartMandateId)}/decision`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json, text/event-stream',
      },
      body: JSON.stringify(body),
    },
  );

  if (!response.ok) {
    let errMsg = `Mandate decision failed (HTTP ${response.status})`;
    try {
      const errJson = asRecord(await response.json());
      if (typeof errJson.detail === 'string') errMsg = errJson.detail;
      else if (typeof errJson.error === 'string') errMsg = errJson.error;
      else if (errJson.detail) errMsg = JSON.stringify(errJson.detail);
    } catch {
      // ignore json parse error
    }
    throw new Error(errMsg);
  }

  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('text/event-stream') && response.body) {
    // Parse SSE response if Gateway streams post-HITL settlement events
    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let match = buffer.match(/\r?\n\r?\n/);
      while (match && match.index !== undefined) {
        const block = buffer.slice(0, match.index);
        buffer = buffer.slice(match.index + match[0].length);
        const ev = parseSSEBlock(block);
        if (ev) {
          if (ev.type === 'text_delta') {
            textChunks += asString(ev.payload.delta ?? ev.payload.text ?? ev.payload.content);
          } else if (ev.type === 'text_done' && !textChunks) {
            textChunks = asString(ev.payload.text ?? ev.payload.content);
          } else if (ev.type === 'a2ui_surface') {
            const surf = normalizeA2UISurface(ev.payload, surfaces.length);
            if (surf) surfaces.push(surf);
          }
        }
        match = buffer.match(/\r?\n\r?\n/);
      }
    }
    reader.releaseLock();
  } else {
    const json = asRecord(await response.json());
    textChunks = asString(
      json.message ??
        json.assistant_message ??
        json.summary ??
        json.reply ??
        json.status_message,
    );

    if (Array.isArray(json.a2ui_surfaces)) {
      json.a2ui_surfaces.forEach((s, idx) => {
        const norm = normalizeA2UISurface(s, idx);
        if (norm) surfaces.push(norm);
      });
    } else if (Array.isArray(json.surfaces)) {
      json.surfaces.forEach((s, idx) => {
        const norm = normalizeA2UISurface(s, idx);
        if (norm) surfaces.push(norm);
      });
    } else if (json.a2ui_surface || json.receipt_surface || json.order_receipt_surface) {
      const norm = normalizeA2UISurface(
        json.a2ui_surface ?? json.receipt_surface ?? json.order_receipt_surface,
        0,
      );
      if (norm) surfaces.push(norm);
    } else if (json.order || json.receipt || (params.decision === 'approve' && json.order_id)) {
      const receiptSource = asRecord(json.receipt ?? json.order ?? json);
      const norm = normalizeA2UISurface(
        {
          surface_type: 'OrderReceiptCard',
          payload: {
            ...receiptSource,
            authorization_path: 'HITL_CART_MANDATE',
            cart_mandate_id: params.cartMandateId,
            signature_b64:
              receiptSource.signature_b64 ?? params.signatureB64,
          },
        },
        0,
      );
      if (norm) surfaces.push(norm);
    }
  }

  return {
    approved: params.decision === 'approve',
    message:
      textChunks ||
      (params.decision === 'approve'
        ? 'Ed25519 CartMandate verified and PaymentMandate settled in ledger.'
        : 'CartMandate rejected. Order cancelled with zero budget impact.'),
    surfaces,
  };
}

/**
 * Fetches aggregated token & FinOps summary (`GET /api/telemetry/summary`).
 */
export async function fetchTelemetrySummary(
  sessionId?: string,
): Promise<TelemetrySummary> {
  try {
    const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
    const res = await fetch(`/api/telemetry/summary${query}`, {
      headers: { Accept: 'application/json' },
    });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    const json = await res.json();
    return normalizeTelemetrySummary(json);
  } catch {
    return {
      totalTokens: 0,
      promptTokens: 0,
      outputTokens: 0,
      cachedTokens: 0,
      estimatedCostUsd: 0,
      a2aHops: 0,
      activeSkills: [],
      perAgent: DEFAULT_AGENT_ROSTER.map((a) => ({ ...a })),
      updatedAt: new Date().toISOString(),
    };
  }
}

/**
 * Fetches distributed OpenTelemetry spans across Gateway (8000) and A2A services (8001–8003).
 */
export async function fetchTelemetrySpans(
  sessionId?: string,
): Promise<TelemetrySpan[]> {
  try {
    const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
    const res = await fetch(`/api/telemetry/spans${query}`, {
      headers: { Accept: 'application/json' },
    });
    if (!res.ok) return [];
    const json = (await res.json()) as unknown;
    const rawList = Array.isArray(json)
      ? json
      : Array.isArray(asRecord(json).spans)
        ? (asRecord(json).spans as unknown[])
        : [];
    return rawList.map((s, idx) => normalizeTelemetrySpan(s, idx));
  } catch {
    return [];
  }
}

/**
 * Helper to turn an SSE `agent_status` event payload into a normalized `AgentActivityPill`.
 */
export function normalizeAgentActivityEvent(
  payload: Record<string, unknown>,
): AgentActivityPill {
  const rawAgent = asString(
    payload.agent ?? payload.agent_name ?? payload.service ?? 'concierge_orchestrator',
  );
  const meta = resolveAgentMetadata(rawAgent);
  const rawStatus = asString(payload.status ?? payload.state, 'running').toLowerCase();
  let status: AgentActivityPill['status'] = 'running';
  if (rawStatus.includes('done') || rawStatus.includes('complete') || rawStatus.includes('ok')) {
    status = 'completed';
  } else if (rawStatus.includes('hitl') || rawStatus.includes('wait') || rawStatus.includes('pause')) {
    status = 'waiting_hitl';
  } else if (rawStatus.includes('err') || rawStatus.includes('fail')) {
    status = 'error';
  }

  return {
    id: `${meta.agentName}-${asNumber(payload.port, meta.port)}`,
    agentName: meta.agentName,
    displayName: asString(payload.display_name, meta.displayName),
    port: asNumber(payload.port, meta.port),
    status,
    detail: asString(payload.detail ?? payload.message ?? payload.tool) || undefined,
    timestamp: new Date().toISOString(),
  };
}
