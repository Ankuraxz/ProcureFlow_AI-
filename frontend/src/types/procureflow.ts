/**
 * Core domain, AP2 Mandate, A2UI Declarative Surface, SSE Event,
 * and OpenTelemetry FinOps contracts for ProcureFlow AI.
 */

export type VendorTier = 'PREFERRED' | 'APPROVED' | 'UNVERIFIED' | 'BLOCKED';

export interface IntentMandateSummary {
  mandateId: string;
  maxAutoUsd: number;
  allowedTiers: VendorTier[];
  maxBudgetUtilization: number;
  status: 'ACTIVE' | 'REVOKED' | 'EXPIRED';
  publicKeyB64?: string;
}

export interface Persona {
  userId: string;
  name: string;
  email: string;
  role: string;
  departmentId: string;
  departmentName: string;
  q4BudgetUsd: number;
  spentUsd: number;
  remainingUsd: number;
  utilizationPct: number;
  intentMandate: IntentMandateSummary;
}

export interface PersonaCryptoKeyInfo {
  userId: string;
  publicKeyB64: string;
  fingerprint: string;
  createdAt: string;
  registeredOnBackend: boolean;
}

export interface ProcurementSession {
  sessionId: string;
  userId: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  messageCount?: number;
}

export type A2UISurfaceType =
  | 'VendorComparisonCard'
  | 'BudgetGaugeCard'
  | 'AP2CartMandateApprovalCard'
  | 'OrderReceiptCard';

export interface VendorComparisonOption {
  optionId: string;
  sku: string;
  name: string;
  vendorId?: string;
  vendorName: string;
  vendorTier: VendorTier;
  unitPriceUsd: number;
  quantity: number;
  volumeDiscountPct: number;
  discountedUnitPriceUsd: number;
  totalPriceUsd: number;
  leadTimeDays: number;
  meetsSla: boolean;
  slaLabel: string;
  stockStatus: string;
  recommended: boolean;
  notes?: string;
}

export interface VendorComparisonData {
  title: string;
  querySummary?: string;
  requestedQuantity?: number;
  requestedSlaDays?: number;
  options: VendorComparisonOption[];
}

export interface BudgetGaugeData {
  departmentId: string;
  departmentName: string;
  fiscalPeriod: string;
  totalBudgetUsd: number;
  currentSpendUsd: number;
  orderAmountUsd: number;
  projectedSpendUsd: number;
  remainingPostOrderUsd: number;
  currentUtilizationPct: number;
  projectedUtilizationPct: number;
  warningThresholdPct: number;
  hardCapPct: number;
  policyVerdict: 'AUTO_OK' | 'HITL_WARNING' | 'HARD_BLOCKED';
  notes?: string;
}

export interface CartLineItem {
  sku: string;
  name: string;
  vendorName: string;
  vendorTier?: VendorTier;
  quantity: number;
  unitPriceUsd: number;
  discountPct: number;
  lineTotalUsd: number;
  leadTimeDays?: number;
}

export interface AP2CartMandateApprovalData {
  cartMandateId: string;
  intentMandateId?: string;
  departmentName: string;
  vendorName: string;
  vendorTier: VendorTier;
  lineItems: CartLineItem[];
  subtotalUsd: number;
  discountTotalUsd: number;
  totalUsd: number;
  currency: string;
  escalationReasons: string[];
  cartSha256: string;
  canonicalPayload: string;
  status: 'PENDING_SIGNATURE' | 'SIGNED_AND_SETTLED' | 'REJECTED' | 'EXPIRED';
  signatureB64?: string;
  signerPublicKeyB64?: string;
  decisionTimestamp?: string;
  rejectionReason?: string;
  expiresAt?: string;
}

export interface DeliveryTimelineStep {
  label: string;
  detail: string;
  status: 'completed' | 'current' | 'upcoming';
}

export interface OrderReceiptData {
  orderId: string;
  status: string;
  authorizationPath: 'AUTO_INTENT_MANDATE' | 'HITL_CART_MANDATE';
  intentMandateId: string;
  cartMandateId?: string;
  paymentMandateId: string;
  vendorName: string;
  vendorTier: VendorTier;
  departmentName: string;
  lineItems: CartLineItem[];
  totalUsd: number;
  currency: string;
  signatureB64: string;
  publicKeyB64?: string;
  cartSha256?: string;
  estimatedDeliveryDays: number;
  estimatedDeliveryDate?: string;
  settledAt: string;
  deliverySteps: DeliveryTimelineStep[];
}

export type A2UISurface =
  | {
      id: string;
      surfaceType: 'VendorComparisonCard';
      data: VendorComparisonData;
    }
  | {
      id: string;
      surfaceType: 'BudgetGaugeCard';
      data: BudgetGaugeData;
    }
  | {
      id: string;
      surfaceType: 'AP2CartMandateApprovalCard';
      data: AP2CartMandateApprovalData;
    }
  | {
      id: string;
      surfaceType: 'OrderReceiptCard';
      data: OrderReceiptData;
    };

export interface AgentActivityPill {
  id: string;
  agentName: string;
  displayName: string;
  port: number;
  status: 'running' | 'completed' | 'waiting_hitl' | 'error';
  detail?: string;
  timestamp: string;
}

export interface SkillActivationPill {
  id: string;
  skillName: string;
  agentName: string;
  level: string;
  timestamp: string;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  createdAt: string;
  isStreaming?: boolean;
  agentActivities: AgentActivityPill[];
  skillsActivated: SkillActivationPill[];
  surfaces: A2UISurface[];
  hitlRequired?: boolean;
  error?: string;
}

export type SSEEventType =
  | 'agent_status'
  | 'skill_activated'
  | 'text_delta'
  | 'text_done'
  | 'a2ui_surface'
  | 'hitl_required'
  | 'done'
  | 'error';

export interface ParsedSSEEvent {
  type: SSEEventType;
  payload: Record<string, unknown>;
}

export interface AgentTelemetryBreakdown {
  agentName: string;
  displayName: string;
  modelName: string;
  port: number;
  promptTokens: number;
  outputTokens: number;
  cachedTokens: number;
  totalTokens: number;
  estimatedCostUsd: number;
  avgLatencyMs: number;
  invocations: number;
}

export interface TelemetrySummary {
  totalTokens: number;
  promptTokens: number;
  outputTokens: number;
  cachedTokens: number;
  estimatedCostUsd: number;
  a2aHops: number;
  activeSkills: string[];
  perAgent: AgentTelemetryBreakdown[];
  updatedAt: string;
}

export interface TelemetrySpan {
  spanId: string;
  traceId: string;
  parentSpanId?: string | null;
  name: string;
  serviceName: string;
  agentName: string;
  port: number;
  kind: string;
  startTimeMs: number;
  endTimeMs: number;
  durationMs: number;
  status: 'OK' | 'ERROR' | 'UNSET';
  attributes: Record<string, unknown>;
}

export interface GuidedScenario {
  id: string;
  title: string;
  badge: string;
  badgeTone: 'emerald' | 'amber' | 'indigo' | 'rose';
  description: string;
  prompt: string;
  suggestedDepartmentHint?: string;
}
