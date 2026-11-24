import React, { useState } from 'react';
import {
  Check,
  Copy,
  Fingerprint,
  History,
  KeyRound,
  MessageSquarePlus,
  Play,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Wallet,
} from 'lucide-react';
import { GUIDED_SCENARIOS } from '../lib/api';
import type {
  GuidedScenario,
  Persona,
  PersonaCryptoKeyInfo,
  ProcurementSession,
} from '../types/procureflow';

interface PersonaSidebarProps {
  personas: Persona[];
  activePersona: Persona;
  cryptoKeyInfo: PersonaCryptoKeyInfo | null;
  backendOnline: boolean;
  sessions: ProcurementSession[];
  activeSessionId: string | null;
  isStreaming: boolean;
  onSelectPersona: (userId: string) => void;
  onRotateKey: () => Promise<void>;
  onResetLedger: () => Promise<void>;
  onNewSession: () => Promise<void>;
  onSelectSession: (sessionId: string) => Promise<void>;
  onRunScenario: (scenario: GuidedScenario) => Promise<void>;
}

function formatUsd(amount: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(amount);
}

const SCENARIO_BADGE_STYLES: Record<GuidedScenario['badgeTone'], string> = {
  emerald: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
  amber: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
  indigo: 'bg-indigo-500/15 text-indigo-300 border-indigo-500/30',
  rose: 'bg-rose-500/15 text-rose-300 border-rose-500/30',
};

export const PersonaSidebar: React.FC<PersonaSidebarProps> = ({
  personas,
  activePersona,
  cryptoKeyInfo,
  backendOnline,
  sessions,
  activeSessionId,
  isStreaming,
  onSelectPersona,
  onRotateKey,
  onResetLedger,
  onNewSession,
  onSelectSession,
  onRunScenario,
}) => {
  const [isResetting, setIsResetting] = useState(false);
  const [resetDone, setResetDone] = useState(false);
  const [copiedKey, setCopiedKey] = useState(false);
  const [isRotatingKey, setIsRotatingKey] = useState(false);

  const utilPct = Math.min(100, Math.max(0, activePersona.utilizationPct));
  const isNearCap = activePersona.utilizationPct >= 80;
  const isOverCap = activePersona.utilizationPct >= 100;

  const handleResetClick = async () => {
    if (isResetting || isStreaming) return;
    setIsResetting(true);
    try {
      await onResetLedger();
      setResetDone(true);
      window.setTimeout(() => setResetDone(false), 2200);
    } finally {
      setIsResetting(false);
    }
  };

  const handleCopyPublicKey = async () => {
    if (!cryptoKeyInfo?.publicKeyB64) return;
    try {
      await navigator.clipboard.writeText(cryptoKeyInfo.publicKeyB64);
      setCopiedKey(true);
      window.setTimeout(() => setCopiedKey(false), 1800);
    } catch {
      // ignore clipboard restrictions
    }
  };

  const handleRotate = async () => {
    if (isRotatingKey || isStreaming) return;
    setIsRotatingKey(true);
    try {
      await onRotateKey();
    } finally {
      setIsRotatingKey(false);
    }
  };

  return (
    <aside className="flex h-full w-80 shrink-0 flex-col border-r border-slate-800 bg-slate-950 text-slate-100 select-none">
      {/* Brand Header */}
      <div className="flex items-center justify-between border-b border-slate-800/90 px-4 py-3.5">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500 to-violet-600 shadow-sm shadow-indigo-500/30">
            <ShieldCheck className="h-4.5 w-4.5 text-white" />
          </div>
          <div>
            <div className="flex items-center gap-1.5">
              <span className="text-sm font-semibold tracking-tight text-white">
                ProcureFlow AI
              </span>
              <span className="rounded bg-indigo-500/20 px-1.5 py-0.5 text-[10px] font-semibold text-indigo-300 border border-indigo-500/30">
                AP2 · A2UI
              </span>
            </div>
            <p className="text-[11px] text-slate-400">Autonomous Enterprise Spend Desk</p>
          </div>
        </div>
        <span
          title={
            backendOnline
              ? 'Connected to FastAPI Gateway (:8000)'
              : 'Preview Mode — Waiting for FastAPI Gateway (:8000)'
          }
          className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium border ${
            backendOnline
              ? 'bg-emerald-500/10 text-emerald-300 border-emerald-500/30'
              : 'bg-amber-500/10 text-amber-300 border-amber-500/30'
          }`}
        >
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              backendOnline ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'
            }`}
          />
          {backendOnline ? ':8000 Live' : 'Preview'}
        </span>
      </div>

      {/* Scrollable Sidebar Body */}
      <div className="dark-scrollbar flex-1 overflow-y-auto px-3.5 py-3 space-y-4">
        {/* Persona Switcher & Department Budget */}
        <section className="rounded-xl border border-slate-800 bg-slate-900/80 p-3 space-y-3 shadow-inner">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
              Active Employee Persona
            </span>
            <span className="text-[10px] font-mono text-slate-500">
              {activePersona.departmentId}
            </span>
          </div>

          <select
            aria-label="Select Employee Persona"
            value={activePersona.userId}
            disabled={isStreaming}
            onChange={(e) => onSelectPersona(e.target.value)}
            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-2 text-xs font-medium text-slate-100 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 disabled:opacity-60"
          >
            {personas.map((p) => (
              <option key={p.userId} value={p.userId}>
                {p.name} — {p.departmentName}
              </option>
            ))}
          </select>

          <div className="flex items-start justify-between gap-2 pt-0.5">
            <div className="min-w-0">
              <p className="truncate text-xs font-semibold text-white">
                {activePersona.role}
              </p>
              <p className="truncate text-[11px] text-slate-400">
                Dept: <span className="text-slate-200">{activePersona.departmentName}</span>
              </p>
            </div>
            <span className="shrink-0 rounded-md bg-slate-800 px-2 py-0.5 text-[10px] font-medium text-slate-300 border border-slate-700">
              Q4 FY26
            </span>
          </div>

          {/* Q4 Budget Utilization Bar */}
          <div className="space-y-1.5 rounded-lg bg-slate-950/80 p-2.5 border border-slate-800/90">
            <div className="flex items-center justify-between text-[11px]">
              <span className="flex items-center gap-1 text-slate-400">
                <Wallet className="h-3.5 w-3.5 text-indigo-400" />
                Q4 Spend Utilization
              </span>
              <span
                className={`font-mono font-semibold ${
                  isOverCap
                    ? 'text-rose-400'
                    : isNearCap
                      ? 'text-amber-300'
                      : 'text-emerald-300'
                }`}
              >
                {activePersona.utilizationPct.toFixed(1)}%
              </span>
            </div>

            <div className="relative h-2 w-full overflow-hidden rounded-full bg-slate-800">
              <div
                className={`h-full rounded-full transition-all duration-500 ${
                  isOverCap
                    ? 'bg-rose-500'
                    : isNearCap
                      ? 'bg-amber-400'
                      : 'bg-gradient-to-r from-indigo-500 to-emerald-400'
                }`}
                style={{ width: `${utilPct}%` }}
              />
              {/* 80% HITL Warning Marker */}
              <div
                title="80% HITL Escalation Threshold"
                className="absolute top-0 bottom-0 w-0.5 bg-amber-300/80"
                style={{ left: '80%' }}
              />
            </div>

            <div className="flex items-center justify-between text-[10px] font-mono text-slate-400">
              <span>
                {formatUsd(activePersona.spentUsd)} / {formatUsd(activePersona.q4BudgetUsd)}
              </span>
              <span className="text-slate-300">
                {formatUsd(activePersona.remainingUsd)} left
              </span>
            </div>
          </div>

          {/* Active AP2 IntentMandate & WebCrypto Ed25519 Signer Badge */}
          <div className="space-y-1.5 rounded-lg border border-emerald-500/25 bg-emerald-950/25 p-2.5">
            <div className="flex items-center gap-1.5 text-[11px] font-semibold text-emerald-300">
              <KeyRound className="h-3.5 w-3.5 shrink-0 text-emerald-400" />
              <span>
                Auto-settle &lt;= ${activePersona.intentMandate.maxAutoUsd} · Verified Ed25519 Key
              </span>
            </div>
            <div className="flex items-center justify-between gap-1 text-[10px] text-slate-400">
              <span className="truncate font-mono text-slate-300">
                Mandate: {activePersona.intentMandate.mandateId}
              </span>
              <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 font-mono text-[9px] text-emerald-300">
                {activePersona.intentMandate.status}
              </span>
            </div>

            {cryptoKeyInfo && (
              <div className="flex items-center justify-between gap-1 pt-1 border-t border-emerald-500/15">
                <div
                  title={`WebCrypto Public Key (Base64): ${cryptoKeyInfo.publicKeyB64}`}
                  className="flex items-center gap-1 min-w-0 text-[10px] font-mono text-indigo-300"
                >
                  <Fingerprint className="h-3 w-3 shrink-0 text-indigo-400" />
                  <span className="truncate">{cryptoKeyInfo.fingerprint}</span>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <button
                    type="button"
                    onClick={handleCopyPublicKey}
                    title="Copy Base64 32-byte Ed25519 Public Key"
                    className="rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-white transition-colors"
                  >
                    {copiedKey ? (
                      <Check className="h-3 w-3 text-emerald-400" />
                    ) : (
                      <Copy className="h-3 w-3" />
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={handleRotate}
                    disabled={isRotatingKey || isStreaming}
                    title="Rotate Browser WebCrypto Ed25519 Keypair"
                    className="rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-white transition-colors disabled:opacity-40"
                  >
                    <RefreshCw
                      className={`h-3 w-3 ${isRotatingKey ? 'animate-spin text-indigo-400' : ''}`}
                    />
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* Reset Demo Ledger Button */}
          <button
            type="button"
            onClick={handleResetClick}
            disabled={isResetting || isStreaming}
            className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-slate-700/90 bg-slate-800/70 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-800 hover:border-slate-600 transition-colors disabled:opacity-50"
          >
            {resetDone ? (
              <>
                <Check className="h-3.5 w-3.5 text-emerald-400" />
                <span className="text-emerald-300">Demo Ledger Reset</span>
              </>
            ) : (
              <>
                <RotateCcw
                  className={`h-3.5 w-3.5 text-slate-400 ${isResetting ? 'animate-spin' : ''}`}
                />
                <span>{isResetting ? 'Resetting Ledger...' : 'Reset Demo Ledger'}</span>
              </>
            )}
          </button>
        </section>

        {/* 1-Click Guided Demo Scenarios */}
        <section className="space-y-2">
          <div className="flex items-center justify-between px-1">
            <span className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
              <Sparkles className="h-3.5 w-3.5 text-indigo-400" />
              1-Click Guided Scenarios
            </span>
            <span className="text-[10px] text-slate-500">5 Flows</span>
          </div>

          <div className="space-y-1.5">
            {GUIDED_SCENARIOS.map((scenario) => (
              <button
                key={scenario.id}
                type="button"
                disabled={isStreaming}
                onClick={() => onRunScenario(scenario)}
                className="group w-full rounded-xl border border-slate-800/90 bg-slate-900/60 p-2.5 text-left hover:border-indigo-500/50 hover:bg-slate-900 transition-all disabled:opacity-50"
              >
                <div className="flex items-center justify-between gap-1.5 mb-1">
                  <span className="text-xs font-semibold text-slate-100 group-hover:text-white flex items-center gap-1">
                    <Play className="h-3 w-3 text-indigo-400 shrink-0 opacity-80 group-hover:opacity-100" />
                    {scenario.title}
                  </span>
                  <span
                    className={`shrink-0 rounded border px-1.5 py-0.5 text-[9px] font-semibold tracking-wide ${SCENARIO_BADGE_STYLES[scenario.badgeTone]}`}
                  >
                    {scenario.badge}
                  </span>
                </div>
                <p className="text-[11px] leading-snug text-slate-400 line-clamp-2">
                  {scenario.description}
                </p>
              </button>
            ))}
          </div>
        </section>

        {/* Persistent SQLite Session History */}
        <section className="space-y-2 pt-1">
          <div className="flex items-center justify-between px-1">
            <span className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
              <History className="h-3.5 w-3.5 text-slate-400" />
              Session History
            </span>
            <button
              type="button"
              disabled={isStreaming}
              onClick={onNewSession}
              className="inline-flex items-center gap-1 rounded-md bg-indigo-600/90 px-2 py-1 text-[11px] font-medium text-white hover:bg-indigo-500 transition-colors disabled:opacity-50"
            >
              <MessageSquarePlus className="h-3 w-3" />
              New
            </button>
          </div>

          <button
            type="button"
            disabled={isStreaming}
            onClick={onNewSession}
            className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-dashed border-slate-700 bg-slate-900/40 px-3 py-2 text-xs font-medium text-slate-300 hover:border-indigo-500/60 hover:text-white transition-colors disabled:opacity-50"
          >
            <MessageSquarePlus className="h-3.5 w-3.5 text-indigo-400" />
            New Procurement Session
          </button>

          <div className="space-y-1">
            {sessions.length === 0 ? (
              <p className="px-2 py-3 text-center text-[11px] text-slate-500">
                No prior SQLite sessions for {activePersona.name.split(' ')[0]}.
              </p>
            ) : (
              sessions.map((sess) => {
                const isActive = sess.sessionId === activeSessionId;
                return (
                  <button
                    key={sess.sessionId}
                    type="button"
                    disabled={isStreaming}
                    onClick={() => onSelectSession(sess.sessionId)}
                    className={`w-full rounded-lg border px-2.5 py-2 text-left transition-colors disabled:opacity-50 ${
                      isActive
                        ? 'border-indigo-500/60 bg-indigo-950/40 text-white'
                        : 'border-transparent bg-slate-900/40 text-slate-300 hover:bg-slate-900 hover:border-slate-800'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-xs font-medium">{sess.title}</span>
                      {sess.messageCount !== undefined && (
                        <span className="shrink-0 rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[9px] text-slate-400">
                          {sess.messageCount}m
                        </span>
                      )}
                    </div>
                    <div className="mt-0.5 flex items-center justify-between text-[10px] font-mono text-slate-500">
                      <span>{sess.sessionId.slice(0, 14)}</span>
                      <span>
                        {new Date(sess.updatedAt).toLocaleTimeString([], {
                          hour: '2-digit',
                          minute: '2-digit',
                        })}
                      </span>
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </section>
      </div>
    </aside>
  );
};
