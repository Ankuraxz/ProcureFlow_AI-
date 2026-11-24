import React, { useState } from 'react';
import {
  AlertTriangle,
  ArrowRight,
  Ban,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  Copy,
  FileCode2,
  Fingerprint,
  KeyRound,
  Lock,
  PackageCheck,
  Percent,
  Receipt,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  TrendingUp,
  Truck,
  XCircle,
} from 'lucide-react';
import { signCanonicalMandate } from '../lib/ed25519';
import type {
  A2UISurface,
  AP2CartMandateApprovalData,
  BudgetGaugeData,
  OrderReceiptData,
  PersonaCryptoKeyInfo,
  VendorComparisonData,
  VendorTier,
} from '../types/procureflow';

function formatCurrency(amount: number, currency = 'USD'): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(amount);
}

const TIER_STYLES: Record<
  VendorTier,
  { badge: string; label: string; icon: React.ReactNode }
> = {
  PREFERRED: {
    badge: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    label: 'PREFERRED',
    icon: <ShieldCheck className="h-3 w-3 text-emerald-600" />,
  },
  APPROVED: {
    badge: 'bg-sky-50 text-sky-700 border-sky-200',
    label: 'APPROVED',
    icon: <CheckCircle2 className="h-3 w-3 text-sky-600" />,
  },
  UNVERIFIED: {
    badge: 'bg-amber-50 text-amber-800 border-amber-300',
    label: 'UNVERIFIED',
    icon: <AlertTriangle className="h-3 w-3 text-amber-600" />,
  },
  BLOCKED: {
    badge: 'bg-rose-50 text-rose-700 border-rose-300',
    label: 'BLOCKED',
    icon: <Ban className="h-3 w-3 text-rose-600" />,
  },
};

export const VendorTierBadge: React.FC<{ tier: VendorTier }> = ({ tier }) => {
  const cfg = TIER_STYLES[tier] ?? TIER_STYLES.APPROVED;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold tracking-wide ${cfg.badge}`}
    >
      {cfg.icon}
      {cfg.label}
    </span>
  );
};

/* ============================================================================
 * 1. VendorComparisonCard
 * ========================================================================== */
const VendorComparisonSurface: React.FC<{
  data: VendorComparisonData;
  onQuickPrompt?: (prompt: string) => void;
}> = ({ data, onQuickPrompt }) => {
  const [selectedId, setSelectedId] = useState<string | null>(
    data.options.find((o) => o.recommended)?.optionId ?? data.options[0]?.optionId ?? null,
  );

  return (
    <div className="animate-fade-in-up overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 bg-slate-50/80 px-4 py-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="rounded-md bg-indigo-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-indigo-700 border border-indigo-200/80">
              A2UI · Sourcing Matrix
            </span>
            <h4 className="text-sm font-semibold text-slate-900">{data.title}</h4>
          </div>
          {data.querySummary && (
            <p className="mt-0.5 text-xs text-slate-500">{data.querySummary}</p>
          )}
        </div>
        {data.requestedSlaDays !== undefined && (
          <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-medium text-slate-700">
            <Clock className="h-3 w-3 text-slate-500" />
            Target SLA: &lt;= {data.requestedSlaDays}d
          </span>
        )}
      </div>

      <div className="grid grid-cols-1 gap-3 p-4 md:grid-cols-2">
        {data.options.map((opt) => {
          const isBlocked = opt.vendorTier === 'BLOCKED';
          const isSelected = selectedId === opt.optionId;

          return (
            <div
              key={opt.optionId}
              onClick={() => !isBlocked && setSelectedId(opt.optionId)}
              className={`relative flex flex-col justify-between rounded-xl border p-3.5 transition-all ${
                isBlocked
                  ? 'border-rose-200 bg-rose-50/30 opacity-85 cursor-not-allowed'
                  : opt.recommended
                    ? 'border-indigo-400 bg-indigo-50/20 ring-1 ring-indigo-400/40 cursor-pointer hover:shadow-md'
                    : isSelected
                      ? 'border-slate-400 bg-slate-50/40 cursor-pointer'
                      : 'border-slate-200 bg-white hover:border-slate-300 cursor-pointer'
              }`}
            >
              {opt.recommended && !isBlocked && (
                <div className="mb-2 flex items-center justify-between">
                  <span className="inline-flex items-center gap-1 rounded-full bg-indigo-600 px-2.5 py-0.5 text-[10px] font-semibold text-white shadow-sm">
                    <Sparkles className="h-3 w-3" />
                    Recommended Best Value
                  </span>
                  <span className="font-mono text-[10px] text-slate-400">{opt.sku}</span>
                </div>
              )}

              {(!opt.recommended || isBlocked) && (
                <div className="mb-1.5 flex items-center justify-between">
                  <span className="font-mono text-[10px] font-medium text-slate-400">
                    {opt.sku}
                  </span>
                  {isBlocked && (
                    <span className="text-[10px] font-semibold text-rose-600">
                      Policy Guardrail Block
                    </span>
                  )}
                </div>
              )}

              <div>
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <h5
                      className={`text-sm font-semibold ${
                        isBlocked ? 'line-through text-slate-500' : 'text-slate-900'
                      }`}
                    >
                      {opt.name}
                    </h5>
                    <p className="text-xs font-medium text-slate-600">{opt.vendorName}</p>
                  </div>
                  <VendorTierBadge tier={opt.vendorTier} />
                </div>

                {/* Badges Row: Volume Discount + Lead-Time SLA Pill */}
                <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                  {opt.volumeDiscountPct > 0 && (
                    <span className="inline-flex items-center gap-1 rounded-md bg-emerald-50 border border-emerald-200 px-2 py-0.5 text-[10px] font-semibold text-emerald-700">
                      <Percent className="h-2.5 w-2.5" />
                      {opt.volumeDiscountPct}% Vol Discount
                    </span>
                  )}
                  <span
                    className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[10px] font-medium ${
                      opt.meetsSla
                        ? 'bg-slate-100 border-slate-200 text-slate-700'
                        : 'bg-amber-50 border-amber-200 text-amber-800'
                    }`}
                  >
                    <Truck className="h-3 w-3" />
                    {opt.slaLabel}
                    {opt.meetsSla ? ' · SLA OK' : ' · Slow'}
                  </span>
                  <span className="rounded-md bg-slate-100 px-2 py-0.5 text-[10px] text-slate-600">
                    {opt.stockStatus}
                  </span>
                </div>

                {opt.notes && (
                  <p className="mt-2 text-[11px] leading-relaxed text-slate-500">
                    {opt.notes}
                  </p>
                )}
              </div>

              <div className="mt-3 flex items-end justify-between border-t border-slate-100 pt-2.5">
                <div>
                  <span className="block text-[10px] text-slate-400">
                    {opt.quantity} × {formatCurrency(opt.discountedUnitPriceUsd)}/unit
                    {opt.volumeDiscountPct > 0 && (
                      <span className="ml-1 line-through text-slate-300">
                        {formatCurrency(opt.unitPriceUsd)}
                      </span>
                    )}
                  </span>
                  <span className="text-base font-bold tracking-tight text-slate-900">
                    {formatCurrency(opt.totalPriceUsd)}
                  </span>
                </div>

                {onQuickPrompt && !isBlocked && (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onQuickPrompt(
                        `Proceed with ${opt.quantity}x ${opt.name} (${opt.sku}) from ${opt.vendorName} at ${formatCurrency(opt.totalPriceUsd)}`,
                      );
                    }}
                    className="inline-flex items-center gap-1 rounded-lg border border-indigo-200 bg-indigo-50 px-2.5 py-1 text-[11px] font-semibold text-indigo-700 hover:bg-indigo-100 transition-colors"
                  >
                    Select Quote
                    <ArrowRight className="h-3 w-3" />
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

/* ============================================================================
 * 2. BudgetGaugeCard
 * ========================================================================== */
const BudgetGaugeSurface: React.FC<{ data: BudgetGaugeData }> = ({ data }) => {
  const currentWidth = Math.min(100, Math.max(0, data.currentUtilizationPct));
  const rawDeltaPct = Math.max(0, data.projectedUtilizationPct - data.currentUtilizationPct);
  const deltaWidth = Math.min(100 - currentWidth, rawDeltaPct);

  const verdictBadge =
    data.policyVerdict === 'HARD_BLOCKED'
      ? {
          cls: 'bg-rose-50 text-rose-700 border-rose-300',
          label: 'HARD CAP EXCEEDED (> 100%)',
          deltaBar: 'bg-rose-500',
        }
      : data.policyVerdict === 'HITL_WARNING'
        ? {
            cls: 'bg-amber-50 text-amber-800 border-amber-300',
            label: `HITL ESCALATION (>= ${data.warningThresholdPct}%)`,
            deltaBar: 'bg-amber-500',
          }
        : {
            cls: 'bg-emerald-50 text-emerald-700 border-emerald-200',
            label: `WITHIN AUTO POLICY (< ${data.warningThresholdPct}%)`,
            deltaBar: 'bg-emerald-500',
          };

  return (
    <div className="animate-fade-in-up overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 bg-slate-50/80 px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="rounded-md bg-slate-200/70 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-slate-700">
            A2UI · FinOps Ledger
          </span>
          <h4 className="text-sm font-semibold text-slate-900">
            {data.departmentName} — {data.fiscalPeriod} Budget Impact
          </h4>
        </div>
        <span
          className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-[10px] font-semibold ${verdictBadge.cls}`}
        >
          <TrendingUp className="h-3 w-3" />
          {verdictBadge.label}
        </span>
      </div>

      <div className="p-4 space-y-4">
        {/* Animated Segmented Progress Bar */}
        <div className="space-y-2">
          <div className="flex items-center justify-between text-xs">
            <span className="font-medium text-slate-600">
              Current:{' '}
              <strong className="font-mono text-slate-900">
                {data.currentUtilizationPct.toFixed(1)}%
              </strong>
            </span>
            <span className="font-medium text-slate-600">
              Projected Post-Order:{' '}
              <strong
                className={`font-mono ${
                  data.policyVerdict === 'HARD_BLOCKED'
                    ? 'text-rose-600'
                    : data.policyVerdict === 'HITL_WARNING'
                      ? 'text-amber-600'
                      : 'text-emerald-600'
                }`}
              >
                {data.projectedUtilizationPct.toFixed(1)}%
              </strong>
            </span>
          </div>

          <div className="relative pt-1 pb-4">
            <div className="relative flex h-4 w-full overflow-hidden rounded-full bg-slate-100 ring-1 ring-inset ring-slate-200">
              {/* Current Spend Segment */}
              <div
                title={`Current Spend: ${formatCurrency(data.currentSpendUsd)} (${data.currentUtilizationPct.toFixed(1)}%)`}
                className="h-full bg-slate-700 transition-all duration-700 ease-out"
                style={{ width: `${currentWidth}%` }}
              />
              {/* Projected Order Delta Segment */}
              <div
                title={`Proposed Order Delta: +${formatCurrency(data.orderAmountUsd)}`}
                className={`h-full striped-progress transition-all duration-700 ease-out ${verdictBadge.deltaBar}`}
                style={{ width: `${deltaWidth}%` }}
              />
            </div>

            {/* 80% HITL Warning Line */}
            <div
              className="absolute top-0 bottom-2 flex flex-col items-center"
              style={{ left: `${Math.min(98, data.warningThresholdPct)}%` }}
            >
              <div className="h-6 w-0.5 bg-amber-500 shadow-sm" />
              <span className="-translate-x-1/2 whitespace-nowrap rounded bg-amber-100 px-1 py-0.2 text-[9px] font-mono font-semibold text-amber-800">
                {data.warningThresholdPct}% HITL
              </span>
            </div>

            {/* 100% Hard Cap Marker */}
            <div className="absolute right-0 top-0 bottom-2 flex flex-col items-end">
              <div className="h-6 w-0.5 bg-rose-500" />
              <span className="whitespace-nowrap text-[9px] font-mono font-semibold text-rose-600">
                100% Cap
              </span>
            </div>
          </div>
        </div>

        {/* 4 Metric Tiles */}
        <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
          <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-2.5">
            <span className="block text-[10px] font-medium uppercase text-slate-400">
              Current Spend
            </span>
            <span className="mt-0.5 block font-mono text-sm font-semibold text-slate-800">
              {formatCurrency(data.currentSpendUsd)}
            </span>
          </div>
          <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-2.5">
            <span className="block text-[10px] font-medium uppercase text-slate-400">
              Order Impact
            </span>
            <span className="mt-0.5 block font-mono text-sm font-semibold text-indigo-600">
              +{formatCurrency(data.orderAmountUsd)}
            </span>
          </div>
          <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-2.5">
            <span className="block text-[10px] font-medium uppercase text-slate-400">
              Post-Order Total
            </span>
            <span className="mt-0.5 block font-mono text-sm font-semibold text-slate-900">
              {formatCurrency(data.projectedSpendUsd)}
            </span>
          </div>
          <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-2.5">
            <span className="block text-[10px] font-medium uppercase text-slate-400">
              Remaining Cap
            </span>
            <span className="mt-0.5 block font-mono text-sm font-semibold text-emerald-700">
              {formatCurrency(data.remainingPostOrderUsd)}
            </span>
          </div>
        </div>

        {data.notes && (
          <p className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600 border border-slate-100">
            {data.notes}
          </p>
        )}
      </div>
    </div>
  );
};

/* ============================================================================
 * 3. AP2CartMandateApprovalCard
 * ========================================================================== */
const AP2CartMandateApprovalSurface: React.FC<{
  data: AP2CartMandateApprovalData;
  userId: string;
  cryptoKeyInfo: PersonaCryptoKeyInfo | null;
  onMandateDecision: (params: {
    cartMandateId: string;
    decision: 'approve' | 'reject';
    signatureB64?: string;
    publicKeyB64?: string;
    canonicalPayload?: string;
    reason?: string;
  }) => Promise<void>;
}> = ({ data, userId, cryptoKeyInfo, onMandateDecision }) => {
  const [showCanonical, setShowCanonical] = useState(false);
  const [copiedHash, setCopiedHash] = useState(false);
  const [busyAction, setBusyAction] = useState<'approve' | 'reject' | null>(null);
  const [localStatus, setLocalStatus] = useState<AP2CartMandateApprovalData['status']>(
    data.status,
  );
  const [localSignatureB64, setLocalSignatureB64] = useState<string | undefined>(
    data.signatureB64,
  );
  const [actionError, setActionError] = useState<string | null>(null);

  const effectiveStatus =
    data.status !== 'PENDING_SIGNATURE' ? data.status : localStatus;
  const effectiveSig = data.signatureB64 || localSignatureB64;

  const handleCopySha = async () => {
    try {
      await navigator.clipboard.writeText(data.cartSha256);
      setCopiedHash(true);
      window.setTimeout(() => setCopiedHash(false), 1800);
    } catch {
      // ignore
    }
  };

  const handleSignAndApprove = async () => {
    if (busyAction || effectiveStatus !== 'PENDING_SIGNATURE') return;
    setBusyAction('approve');
    setActionError(null);
    try {
      const { signatureB64, publicKeyB64 } = await signCanonicalMandate(
        userId,
        data.canonicalPayload,
      );
      await onMandateDecision({
        cartMandateId: data.cartMandateId,
        decision: 'approve',
        signatureB64,
        publicKeyB64,
        canonicalPayload: data.canonicalPayload,
      });
      setLocalSignatureB64(signatureB64);
      setLocalStatus('SIGNED_AND_SETTLED');
    } catch (err) {
      setActionError(
        err instanceof Error ? err.message : 'Failed to sign and authorize CartMandate.',
      );
    } finally {
      setBusyAction(null);
    }
  };

  const handleReject = async () => {
    if (busyAction || effectiveStatus !== 'PENDING_SIGNATURE') return;
    setBusyAction('reject');
    setActionError(null);
    try {
      await onMandateDecision({
        cartMandateId: data.cartMandateId,
        decision: 'reject',
        reason: 'Rejected by human approver in AP2 HITL console.',
      });
      setLocalStatus('REJECTED');
    } catch (err) {
      setActionError(
        err instanceof Error ? err.message : 'Failed to submit mandate rejection.',
      );
    } finally {
      setBusyAction(null);
    }
  };

  let formattedCanonical = data.canonicalPayload;
  try {
    formattedCanonical = JSON.stringify(JSON.parse(data.canonicalPayload), null, 2);
  } catch {
    // Keep exact string if not JSON-formatted
  }

  return (
    <div className="animate-fade-in-up overflow-hidden rounded-2xl border-2 border-amber-300/90 bg-white shadow-md">
      {/* High-Trust Cryptographic Gate Header */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-amber-200 bg-gradient-to-r from-amber-50 via-amber-50/60 to-indigo-50/40 px-4 py-3">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-amber-500 text-white shadow-sm">
            <KeyRound className="h-4 w-4" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-xs font-bold uppercase tracking-wider text-amber-900">
                AP2 CartMandate · Cryptographic HITL Gate
              </span>
              <span className="font-mono text-[11px] font-semibold text-slate-600">
                {data.cartMandateId}
              </span>
            </div>
            <p className="text-[11px] text-slate-600">
              Dept: <strong>{data.departmentName}</strong> · Supplier:{' '}
              <strong>{data.vendorName}</strong>
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <VendorTierBadge tier={data.vendorTier} />
          {effectiveStatus === 'PENDING_SIGNATURE' && (
            <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 border border-amber-300 px-2.5 py-0.5 text-[10px] font-bold text-amber-900">
              <ShieldAlert className="h-3 w-3 text-amber-700" />
              AWAITING ED25519 SIGNATURE
            </span>
          )}
          {effectiveStatus === 'SIGNED_AND_SETTLED' && (
            <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 border border-emerald-300 px-2.5 py-0.5 text-[10px] font-bold text-emerald-800">
              <CheckCircle2 className="h-3 w-3 text-emerald-700" />
              SIGNED &amp; AUTHORIZED
            </span>
          )}
          {effectiveStatus === 'REJECTED' && (
            <span className="inline-flex items-center gap-1 rounded-full bg-rose-100 border border-rose-300 px-2.5 py-0.5 text-[10px] font-bold text-rose-800">
              <XCircle className="h-3 w-3 text-rose-700" />
              REJECTED BY APPROVER
            </span>
          )}
        </div>
      </div>

      <div className="p-4 space-y-4">
        {/* Escalation Trigger Badges */}
        <div className="space-y-1.5">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
            HITL Escalation Triggers
          </span>
          <div className="flex flex-wrap gap-1.5">
            {data.escalationReasons.map((reason, idx) => (
              <span
                key={idx}
                className="inline-flex items-center gap-1.5 rounded-lg border border-amber-200 bg-amber-50/90 px-2.5 py-1 text-xs font-medium text-amber-900"
              >
                <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-600" />
                {reason}
              </span>
            ))}
          </div>
        </div>

        {/* Itemized Line Items Table */}
        <div className="overflow-hidden rounded-xl border border-slate-200">
          <table className="w-full text-left text-xs">
            <thead className="bg-slate-50 text-[10px] font-semibold uppercase tracking-wider text-slate-500 border-b border-slate-200">
              <tr>
                <th className="px-3 py-2">SKU / Item</th>
                <th className="px-3 py-2 text-right">Qty</th>
                <th className="px-3 py-2 text-right">Unit Price</th>
                <th className="px-3 py-2 text-right">Disc</th>
                <th className="px-3 py-2 text-right">Line Total</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 bg-white">
              {data.lineItems.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-3 py-2.5 text-slate-500">
                    Consolidated cart settlement for {data.vendorName}
                  </td>
                </tr>
              ) : (
                data.lineItems.map((item, idx) => (
                  <tr key={`${item.sku}-${idx}`}>
                    <td className="px-3 py-2">
                      <div className="font-medium text-slate-900">{item.name}</div>
                      <div className="font-mono text-[10px] text-slate-400">{item.sku}</div>
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-slate-700">
                      {item.quantity}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-slate-700">
                      {formatCurrency(item.unitPriceUsd, data.currency)}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-emerald-600">
                      {item.discountPct > 0 ? `-${item.discountPct}%` : '—'}
                    </td>
                    <td className="px-3 py-2 text-right font-mono font-semibold text-slate-900">
                      {formatCurrency(item.lineTotalUsd, data.currency)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
            <tfoot className="border-t border-slate-200 bg-slate-50/90">
              <tr>
                <td colSpan={4} className="px-3 py-2.5 text-right text-xs font-semibold text-slate-700">
                  Authorized Cart Total ({data.currency})
                </td>
                <td className="px-3 py-2.5 text-right font-mono text-sm font-bold text-slate-900">
                  {formatCurrency(data.totalUsd, data.currency)}
                </td>
              </tr>
            </tfoot>
          </table>
        </div>

        {/* Cryptographic Digest & Canonical Payload Inspector */}
        <div className="rounded-xl border border-slate-200 bg-slate-950 p-3 text-slate-200 space-y-2.5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-1.5 text-[11px] font-semibold text-indigo-300">
              <Lock className="h-3.5 w-3.5 text-indigo-400" />
              Canonical Cart SHA-256 Digest
            </div>
            {cryptoKeyInfo && (
              <span className="inline-flex items-center gap-1 rounded bg-slate-900 border border-slate-800 px-2 py-0.5 font-mono text-[10px] text-emerald-300">
                <Fingerprint className="h-3 w-3 text-emerald-400" />
                Signer: {cryptoKeyInfo.fingerprint}
              </span>
            )}
          </div>

          <div className="flex items-center justify-between gap-2 rounded-lg bg-slate-900 px-2.5 py-1.5 border border-slate-800">
            <code className="truncate font-mono text-[11px] text-amber-300">
              {data.cartSha256}
            </code>
            <button
              type="button"
              onClick={handleCopySha}
              className="inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[10px] text-slate-400 hover:bg-slate-800 hover:text-white"
            >
              {copiedHash ? (
                <>
                  <Check className="h-3 w-3 text-emerald-400" />
                  Copied
                </>
              ) : (
                <>
                  <Copy className="h-3 w-3" />
                  Copy
                </>
              )}
            </button>
          </div>

          {/* Expandable Canonical JSON Payload Preview */}
          <div>
            <button
              type="button"
              onClick={() => setShowCanonical((prev) => !prev)}
              className="flex items-center gap-1 text-[11px] font-medium text-slate-400 hover:text-white transition-colors"
            >
              {showCanonical ? (
                <ChevronDown className="h-3.5 w-3.5" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5" />
              )}
              <FileCode2 className="h-3.5 w-3.5 text-indigo-400" />
              <span>
                {showCanonical ? 'Hide' : 'Inspect'} Canonical UTF-8 Mandate Payload (
                {new TextEncoder().encode(data.canonicalPayload).byteLength} bytes)
              </span>
            </button>

            {showCanonical && (
              <div className="mt-2 space-y-1.5">
                <pre className="max-h-44 overflow-auto rounded-lg border border-slate-800 bg-slate-900/90 p-2.5 font-mono text-[11px] leading-relaxed text-emerald-200">
                  {formattedCanonical}
                </pre>
                <p className="text-[10px] text-slate-400">
                  Exact UTF-8 byte sequence signed via{' '}
                  <code className="text-indigo-300">
                    crypto.subtle.sign(&#123; name: &quot;Ed25519&quot; &#125;)
                  </code>
                  .
                </p>
              </div>
            )}
          </div>

          {effectiveSig && (
            <div className="border-t border-slate-800 pt-2">
              <span className="block text-[10px] font-semibold uppercase tracking-wider text-emerald-400">
                Verified 64-Byte Ed25519 Signature (Base64)
              </span>
              <code className="mt-0.5 block break-all font-mono text-[10px] text-slate-300">
                {effectiveSig}
              </code>
            </div>
          )}
        </div>

        {actionError && (
          <div className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
            {actionError}
          </div>
        )}

        {/* Interactive Decision Buttons */}
        {effectiveStatus === 'PENDING_SIGNATURE' && (
          <div className="flex flex-wrap items-center justify-end gap-2.5 pt-1">
            <button
              type="button"
              disabled={busyAction !== null}
              onClick={handleReject}
              className="inline-flex items-center gap-1.5 rounded-xl border border-rose-200 bg-white px-4 py-2.5 text-xs font-semibold text-rose-700 hover:bg-rose-50 transition-colors disabled:opacity-50"
            >
              <XCircle className="h-4 w-4" />
              {busyAction === 'reject' ? 'Rejecting...' : 'Reject Order'}
            </button>

            <button
              type="button"
              disabled={busyAction !== null}
              onClick={handleSignAndApprove}
              className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-indigo-600 to-violet-600 px-4 py-2.5 text-xs font-semibold text-white shadow-sm shadow-indigo-500/25 hover:from-indigo-500 hover:to-violet-500 transition-all disabled:opacity-60"
            >
              <KeyRound className="h-4 w-4" />
              {busyAction === 'approve'
                ? 'Signing with WebCrypto Ed25519...'
                : 'Sign CartMandate (Ed25519) & Authorize Payment'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
};

/* ============================================================================
 * 4. OrderReceiptCard
 * ========================================================================== */
const OrderReceiptSurface: React.FC<{ data: OrderReceiptData }> = ({ data }) => {
  const isAuto = data.authorizationPath === 'AUTO_INTENT_MANDATE';

  return (
    <div className="animate-fade-in-up overflow-hidden rounded-2xl border border-emerald-200 bg-white shadow-sm">
      {/* Verified Receipt Header */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-emerald-100 bg-gradient-to-r from-emerald-50/90 to-teal-50/50 px-4 py-3">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-600 text-white shadow-sm">
            <Receipt className="h-4 w-4" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h4 className="text-sm font-bold text-slate-900">
                AP2 Verified Order Receipt
              </h4>
              <span className="rounded bg-emerald-600/10 border border-emerald-600/25 px-2 py-0.5 font-mono text-[11px] font-semibold text-emerald-800">
                {data.orderId}
              </span>
            </div>
            <p className="text-[11px] text-slate-600">
              Supplier: <strong>{data.vendorName}</strong> · Dept:{' '}
              <strong>{data.departmentName}</strong>
            </p>
          </div>
        </div>

        {/* Authorization Path Pill */}
        <span
          className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[10px] font-bold tracking-wide ${
            isAuto
              ? 'bg-emerald-100/80 border-emerald-300 text-emerald-800'
              : 'bg-indigo-100/80 border-indigo-300 text-indigo-800'
          }`}
        >
          <ShieldCheck className="h-3.5 w-3.5" />
          {data.authorizationPath}
        </span>
      </div>

      <div className="p-4 space-y-4">
        {/* AP2 Verifiable Three-Mandate Chain */}
        <div className="rounded-xl border border-slate-200 bg-slate-50/80 p-3">
          <span className="block text-[10px] font-semibold uppercase tracking-wider text-slate-400 mb-2">
            AP2 Cryptographic Mandate Chain
          </span>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <div className="flex-1 min-w-[140px] rounded-lg border border-slate-200 bg-white px-2.5 py-2">
              <span className="block text-[10px] font-semibold text-slate-400">
                1. IntentMandate
              </span>
              <span className="font-mono text-[11px] font-semibold text-slate-800">
                {data.intentMandateId}
              </span>
            </div>

            <ArrowRight className="h-4 w-4 shrink-0 text-slate-400" />

            <div
              className={`flex-1 min-w-[140px] rounded-lg border px-2.5 py-2 ${
                data.cartMandateId
                  ? 'border-indigo-200 bg-indigo-50/40'
                  : 'border-dashed border-slate-200 bg-white/60'
              }`}
            >
              <span className="block text-[10px] font-semibold text-slate-400">
                2. CartMandate
              </span>
              <span className="font-mono text-[11px] font-semibold text-slate-800">
                {data.cartMandateId || 'Auto-Bypassed (<= $500)'}
              </span>
            </div>

            <ArrowRight className="h-4 w-4 shrink-0 text-slate-400" />

            <div className="flex-1 min-w-[140px] rounded-lg border border-emerald-200 bg-emerald-50/50 px-2.5 py-2">
              <span className="block text-[10px] font-semibold text-emerald-700">
                3. PaymentMandate
              </span>
              <span className="font-mono text-[11px] font-semibold text-emerald-900">
                {data.paymentMandateId}
              </span>
            </div>
          </div>
        </div>

        {/* Summary & Signature Proof */}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="rounded-xl border border-slate-200 p-3 flex flex-col justify-between">
            <div>
              <span className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                Settled Ledger Amount
              </span>
              <div className="mt-1 flex items-baseline gap-2">
                <span className="font-mono text-xl font-bold text-slate-900">
                  {formatCurrency(data.totalUsd, data.currency)}
                </span>
                <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-700 border border-emerald-200">
                  {data.status}
                </span>
              </div>
            </div>
            {data.lineItems.length > 0 && (
              <div className="mt-2 border-t border-slate-100 pt-2 space-y-1">
                {data.lineItems.map((li, idx) => (
                  <div
                    key={idx}
                    className="flex items-center justify-between text-[11px] text-slate-600"
                  >
                    <span className="truncate">
                      {li.quantity}× {li.name}
                    </span>
                    <span className="font-mono font-medium text-slate-800">
                      {formatCurrency(li.lineTotalUsd, data.currency)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="rounded-xl border border-slate-800 bg-slate-950 p-3 text-slate-200 flex flex-col justify-between">
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-indigo-300">
                Ed25519 Signature Proof
              </span>
              <span className="inline-flex items-center gap-1 rounded bg-emerald-500/20 px-1.5 py-0.5 font-mono text-[9px] font-semibold text-emerald-300">
                <CheckCircle2 className="h-2.5 w-2.5" />
                VERIFIED
              </span>
            </div>
            <code className="my-1.5 line-clamp-2 break-all font-mono text-[10px] text-slate-300">
              {data.signatureB64}
            </code>
            <div className="flex items-center justify-between text-[10px] text-slate-400">
              <span>Settled: {new Date(data.settledAt).toLocaleTimeString()}</span>
              <span>ETA: {data.estimatedDeliveryDays} business days</span>
            </div>
          </div>
        </div>

        {/* Delivery Timeline */}
        <div className="rounded-xl border border-slate-100 bg-slate-50/50 p-3">
          <div className="mb-2.5 flex items-center gap-1.5 text-[11px] font-semibold text-slate-700">
            <PackageCheck className="h-3.5 w-3.5 text-emerald-600" />
            Fulfillment &amp; Delivery Timeline
          </div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-4">
            {data.deliverySteps.map((step, idx) => (
              <div
                key={idx}
                className={`rounded-lg border p-2 ${
                  step.status === 'completed'
                    ? 'border-emerald-200 bg-emerald-50/40'
                    : step.status === 'current'
                      ? 'border-indigo-200 bg-indigo-50/40'
                      : 'border-slate-200 bg-white'
                }`}
              >
                <div className="flex items-center gap-1.5">
                  <span
                    className={`h-2 w-2 rounded-full ${
                      step.status === 'completed'
                        ? 'bg-emerald-500'
                        : step.status === 'current'
                          ? 'bg-indigo-500 animate-pulse'
                          : 'bg-slate-300'
                    }`}
                  />
                  <span className="text-[11px] font-semibold text-slate-800">
                    {step.label}
                  </span>
                </div>
                {step.detail && (
                  <p className="mt-1 text-[10px] text-slate-500">{step.detail}</p>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};

/* ============================================================================
 * Root Declarative A2UI Surface Dispatcher
 * ========================================================================== */
export interface A2UIRendererProps {
  surface: A2UISurface;
  userId: string;
  cryptoKeyInfo: PersonaCryptoKeyInfo | null;
  onQuickPrompt?: (prompt: string) => void;
  onMandateDecision: (params: {
    cartMandateId: string;
    decision: 'approve' | 'reject';
    signatureB64?: string;
    publicKeyB64?: string;
    canonicalPayload?: string;
    reason?: string;
  }) => Promise<void>;
}

export const A2UIRenderer: React.FC<A2UIRendererProps> = ({
  surface,
  userId,
  cryptoKeyInfo,
  onQuickPrompt,
  onMandateDecision,
}) => {
  switch (surface.surfaceType) {
    case 'VendorComparisonCard':
      return (
        <VendorComparisonSurface
          data={surface.data}
          onQuickPrompt={onQuickPrompt}
        />
      );
    case 'BudgetGaugeCard':
      return <BudgetGaugeSurface data={surface.data} />;
    case 'AP2CartMandateApprovalCard':
      return (
        <AP2CartMandateApprovalSurface
          data={surface.data}
          userId={userId}
          cryptoKeyInfo={cryptoKeyInfo}
          onMandateDecision={onMandateDecision}
        />
      );
    case 'OrderReceiptCard':
      return <OrderReceiptSurface data={surface.data} />;
    default:
      return null;
  }
};
