import React, { useEffect, useRef, useState } from 'react';
import {
  Activity,
  AlertCircle,
  BookOpen,
  Bot,
  Cpu,
  KeyRound,
  Loader2,
  Network,
  PanelRightClose,
  PanelRightOpen,
  Send,
  ShieldCheck,
  Square,
  User,
} from 'lucide-react';
import { GUIDED_SCENARIOS } from '../lib/api';
import type {
  AgentActivityPill,
  ChatMessage,
  GuidedScenario,
  Persona,
  PersonaCryptoKeyInfo,
} from '../types/procureflow';
import { A2UIRenderer } from './A2UIRenderer';

interface ChatWorkspaceProps {
  activePersona: Persona;
  cryptoKeyInfo: PersonaCryptoKeyInfo | null;
  activeSessionId: string | null;
  messages: ChatMessage[];
  isStreaming: boolean;
  telemetryDrawerOpen: boolean;
  totalTokensBadge: number;
  onToggleTelemetryDrawer: () => void;
  onSendMessage: (text: string) => Promise<void>;
  onStopStreaming: () => void;
  onRunScenario: (scenario: GuidedScenario) => Promise<void>;
  onMandateDecision: (params: {
    cartMandateId: string;
    decision: 'approve' | 'reject';
    signatureB64?: string;
    publicKeyB64?: string;
    canonicalPayload?: string;
    reason?: string;
  }) => Promise<void>;
}

/**
 * Lightweight, safe inline rich-text formatter supporting **bold** and `code` spans.
 */
function renderInlineFormatted(line: string): React.ReactNode[] {
  const parts = line.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return parts.map((part, idx) => {
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
      return (
        <strong key={idx} className="font-semibold text-slate-900">
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
      return (
        <code
          key={idx}
          className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[12px] text-indigo-700 border border-slate-200/80"
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    return <React.Fragment key={idx}>{part}</React.Fragment>;
  });
}

const FormattedMessageText: React.FC<{ content: string }> = ({ content }) => {
  const lines = content.split('\n');
  return (
    <div className="space-y-1.5 text-sm leading-relaxed text-slate-700">
      {lines.map((line, i) => {
        const trimmed = line.trim();
        if (!trimmed) {
          return <div key={i} className="h-1" />;
        }
        if (trimmed.startsWith('### ')) {
          return (
            <h4 key={i} className="pt-1 text-sm font-bold text-slate-900">
              {renderInlineFormatted(trimmed.slice(4))}
            </h4>
          );
        }
        if (trimmed.startsWith('## ')) {
          return (
            <h3 key={i} className="pt-1 text-base font-bold text-slate-900">
              {renderInlineFormatted(trimmed.slice(3))}
            </h3>
          );
        }
        if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
          return (
            <div key={i} className="flex items-start gap-2 pl-1">
              <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-indigo-500" />
              <span>{renderInlineFormatted(trimmed.slice(2))}</span>
            </div>
          );
        }
        return <p key={i}>{renderInlineFormatted(line)}</p>;
      })}
    </div>
  );
};

const AgentStatusBadge: React.FC<{ pill: AgentActivityPill }> = ({ pill }) => {
  const styles =
    pill.status === 'running'
      ? 'border-indigo-200 bg-indigo-50 text-indigo-700'
      : pill.status === 'waiting_hitl'
        ? 'border-amber-300 bg-amber-50 text-amber-800'
        : pill.status === 'error'
          ? 'border-rose-200 bg-rose-50 text-rose-700'
          : 'border-emerald-200 bg-emerald-50/70 text-emerald-800';

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium transition-all ${styles}`}
    >
      {pill.status === 'running' ? (
        <Loader2 className="h-3 w-3 animate-spin text-indigo-600" />
      ) : (
        <span
          className={`h-1.5 w-1.5 rounded-full ${
            pill.status === 'waiting_hitl'
              ? 'bg-amber-500 animate-pulse'
              : pill.status === 'error'
                ? 'bg-rose-500'
                : 'bg-emerald-500'
          }`}
        />
      )}
      <span>
        {pill.displayName} ({pill.port}) · {pill.status.replace('_', ' ')}
      </span>
      {pill.detail && (
        <span className="max-w-[160px] truncate text-[10px] opacity-80">
          — {pill.detail}
        </span>
      )}
    </span>
  );
};

export const ChatWorkspace: React.FC<ChatWorkspaceProps> = ({
  activePersona,
  cryptoKeyInfo,
  activeSessionId,
  messages,
  isStreaming,
  telemetryDrawerOpen,
  totalTokensBadge,
  onToggleTelemetryDrawer,
  onSendMessage,
  onStopStreaming,
  onRunScenario,
  onMandateDecision,
}) => {
  const [input, setInput] = useState('');
  const scrollEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    scrollEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages, isStreaming]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = input.trim();
    if (!trimmed || isStreaming) return;
    setInput('');
    await onSendMessage(trimmed);
  };

  const handleKeyDown = async (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      const trimmed = input.trim();
      if (!trimmed || isStreaming) return;
      setInput('');
      await onSendMessage(trimmed);
    }
  };

  return (
    <main className="flex min-w-0 flex-1 flex-col bg-slate-50/70">
      {/* Workspace Header Bar */}
      <header className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-slate-200 bg-white px-5">
        <div className="flex items-center gap-3 min-w-0">
          <div className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs font-medium text-slate-700">
            <Network className="h-3.5 w-3.5 text-indigo-600" />
            <span>A2A Mesh</span>
            <span className="font-mono text-[10px] text-slate-400">
              8000 → 8001·8002·8003
            </span>
          </div>

          {activeSessionId && (
            <span className="hidden sm:inline-flex items-center gap-1 rounded-md bg-slate-100 px-2 py-1 font-mono text-[11px] text-slate-600">
              Session: {activeSessionId.slice(0, 14)}
            </span>
          )}
        </div>

        <div className="flex items-center gap-2.5">
          {cryptoKeyInfo && (
            <span className="hidden lg:inline-flex items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50/80 px-2.5 py-1 text-[11px] font-medium text-emerald-800">
              <KeyRound className="h-3 w-3 text-emerald-600" />
              <span className="font-mono text-[10px]">{cryptoKeyInfo.fingerprint}</span>
            </span>
          )}

          <button
            type="button"
            onClick={onToggleTelemetryDrawer}
            className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-semibold transition-colors ${
              telemetryDrawerOpen
                ? 'border-indigo-200 bg-indigo-50 text-indigo-700'
                : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
            }`}
          >
            <Activity className="h-3.5 w-3.5 text-indigo-600" />
            <span>FinOps &amp; Traces</span>
            {totalTokensBadge > 0 && (
              <span className="rounded-full bg-indigo-600 px-1.5 py-0.2 font-mono text-[10px] text-white">
                {totalTokensBadge.toLocaleString()} tok
              </span>
            )}
            {telemetryDrawerOpen ? (
              <PanelRightClose className="h-3.5 w-3.5 ml-0.5 text-slate-500" />
            ) : (
              <PanelRightOpen className="h-3.5 w-3.5 ml-0.5 text-slate-500" />
            )}
          </button>
        </div>
      </header>

      {/* Center Conversational + Declarative A2UI Stream */}
      <div className="flex-1 overflow-y-auto px-4 py-6 sm:px-8">
        <div className="mx-auto max-w-4xl space-y-6">
          {messages.length === 0 ? (
            <div className="space-y-6 py-6">
              <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
                <div className="flex items-start gap-4">
                  <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-600 to-violet-600 text-white shadow-md shadow-indigo-500/20">
                    <ShieldCheck className="h-6 w-6" />
                  </div>
                  <div>
                    <h2 className="text-lg font-bold tracking-tight text-slate-900">
                      Welcome, {activePersona.name} ({activePersona.departmentName})
                    </h2>
                    <p className="mt-1 text-sm text-slate-600 leading-relaxed">
                      Source enterprise hardware and software licenses through our{' '}
                      <strong>Google ADK Multi-Agent A2A Mesh</strong>. Quotes under{' '}
                      <strong>
                        ${activePersona.intentMandate.maxAutoUsd.toFixed(0)}
                      </strong>{' '}
                      from allowlisted vendors settle autonomously via your standing{' '}
                      <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs text-indigo-700">
                        IntentMandate
                      </code>
                      ; higher-risk orders escalate to an interactive{' '}
                      <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs text-amber-800">
                        CartMandate
                      </code>{' '}
                      signed in-browser with your WebCrypto Ed25519 private key.
                    </p>
                  </div>
                </div>

                {/* Architectural Pillar Mini-Cards */}
                <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
                  <div className="rounded-xl border border-slate-100 bg-slate-50/80 p-3">
                    <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-800">
                      <Cpu className="h-3.5 w-3.5 text-indigo-600" />
                      4 Cooperating ADK Agents
                    </div>
                    <p className="mt-1 text-[11px] text-slate-500">
                      Orchestrator (:8000) fans out over A2A JSON-RPC to Catalog (:8001),
                      Policy (:8002), and AP2 Settlement (:8003).
                    </p>
                  </div>
                  <div className="rounded-xl border border-slate-100 bg-slate-50/80 p-3">
                    <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-800">
                      <BookOpen className="h-3.5 w-3.5 text-violet-600" />
                      Lazy Procedural Skills
                    </div>
                    <p className="mt-1 text-[11px] text-slate-500">
                      SLMs load L1 metadata at startup and activate L2{' '}
                      <code className="font-mono">SKILL.md</code> playbooks on demand.
                    </p>
                  </div>
                  <div className="rounded-xl border border-slate-100 bg-slate-50/80 p-3">
                    <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-800">
                      <KeyRound className="h-3.5 w-3.5 text-emerald-600" />
                      WebCrypto Ed25519 AP2
                    </div>
                    <p className="mt-1 text-[11px] text-slate-500">
                      Canonical SHA-256 cart digests verified with real 64-byte Ed25519
                      digital signatures.
                    </p>
                  </div>
                </div>
              </div>

              {/* Quick-Start Scenario Grid */}
              <div className="space-y-2.5">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400 px-1">
                  Launch a Guided Procurement Scenario
                </h3>
                <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                  {GUIDED_SCENARIOS.slice(0, 4).map((sc) => (
                    <button
                      key={sc.id}
                      type="button"
                      disabled={isStreaming}
                      onClick={() => onRunScenario(sc)}
                      className="flex flex-col justify-between rounded-xl border border-slate-200 bg-white p-3.5 text-left shadow-sm hover:border-indigo-400 hover:shadow transition-all disabled:opacity-50"
                    >
                      <div>
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-xs font-bold text-slate-900">
                            {sc.title}
                          </span>
                          <span className="rounded bg-slate-100 px-2 py-0.5 text-[10px] font-semibold text-slate-700">
                            {sc.badge}
                          </span>
                        </div>
                        <p className="mt-1.5 text-xs text-slate-600 line-clamp-2">
                          &ldquo;{sc.prompt}&rdquo;
                        </p>
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            messages.map((msg) => {
              if (msg.role === 'user') {
                return (
                  <div key={msg.id} className="flex justify-end">
                    <div className="flex max-w-2xl items-start gap-3">
                      <div className="rounded-2xl rounded-tr-sm bg-slate-900 px-4 py-3 text-sm text-white shadow-sm">
                        <p className="whitespace-pre-wrap leading-relaxed">
                          {msg.content}
                        </p>
                      </div>
                      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-indigo-100 text-indigo-700 border border-indigo-200">
                        <User className="h-4 w-4" />
                      </div>
                    </div>
                  </div>
                );
              }

              return (
                <div key={msg.id} className="flex items-start gap-3">
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-600 to-violet-600 text-white shadow-sm">
                    <Bot className="h-4 w-4" />
                  </div>

                  <div className="min-w-0 flex-1 space-y-3">
                    {/* Live A2A Agent Activity & SkillToolset Pills */}
                    {(msg.agentActivities.length > 0 ||
                      msg.skillsActivated.length > 0) && (
                      <div className="flex flex-wrap items-center gap-1.5">
                        {msg.agentActivities.map((act) => (
                          <AgentStatusBadge key={act.id} pill={act} />
                        ))}
                        {msg.skillsActivated.map((sk) => (
                          <span
                            key={sk.id}
                            className="inline-flex items-center gap-1 rounded-full border border-violet-200 bg-violet-50 px-2.5 py-0.5 text-[11px] font-medium text-violet-800"
                          >
                            <BookOpen className="h-3 w-3 text-violet-600" />
                            Skill loaded: <code className="font-mono">{sk.skillName}</code>
                          </span>
                        ))}
                      </div>
                    )}

                    {/* Conversational Assistant Bubble */}
                    {(msg.content || msg.isStreaming) && (
                      <div className="rounded-2xl rounded-tl-sm border border-slate-200 bg-white px-4 py-3.5 shadow-sm">
                        {msg.content ? (
                          <FormattedMessageText content={msg.content} />
                        ) : (
                          <div className="flex items-center gap-2 text-xs font-medium text-slate-500">
                            <Loader2 className="h-3.5 w-3.5 animate-spin text-indigo-600" />
                            <span>
                              Coordinating A2A Sourcing, Policy &amp; AP2 Settlement agents...
                            </span>
                          </div>
                        )}
                      </div>
                    )}

                    {/* Error Banner */}
                    {msg.error && (
                      <div className="flex items-start gap-2.5 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-xs text-rose-800">
                        <AlertCircle className="h-4 w-4 shrink-0 text-rose-600 mt-0.5" />
                        <div>
                          <p className="font-semibold">Stream / Gateway Error</p>
                          <p className="mt-0.5 text-rose-700">{msg.error}</p>
                        </div>
                      </div>
                    )}

                    {/* Declarative A2UI Surfaces */}
                    {msg.surfaces.length > 0 && (
                      <div className="space-y-3.5">
                        {msg.surfaces.map((surface) => (
                          <A2UIRenderer
                            key={surface.id}
                            surface={surface}
                            userId={activePersona.userId}
                            cryptoKeyInfo={cryptoKeyInfo}
                            onQuickPrompt={(prompt) => {
                              if (!isStreaming) {
                                void onSendMessage(prompt);
                              }
                            }}
                            onMandateDecision={onMandateDecision}
                          />
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              );
            })
          )}
          <div ref={scrollEndRef} />
        </div>
      </div>

      {/* Bottom Prompt Composer */}
      <div className="shrink-0 border-t border-slate-200 bg-white px-4 py-3.5 sm:px-8">
        <form onSubmit={handleSubmit} className="mx-auto max-w-4xl">
          <div className="flex items-end gap-2 rounded-2xl border border-slate-300 bg-slate-50/70 p-2 focus-within:border-indigo-500 focus-within:bg-white focus-within:ring-2 focus-within:ring-indigo-500/20 transition-all">
            <textarea
              rows={1}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={`Ask ProcureFlow Concierge as ${activePersona.name} (${activePersona.departmentName})...`}
              className="max-h-32 min-h-[38px] flex-1 resize-none bg-transparent px-2.5 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus:outline-none"
            />

            {isStreaming ? (
              <button
                type="button"
                onClick={onStopStreaming}
                className="inline-flex h-9 items-center gap-1.5 rounded-xl bg-rose-600 px-3.5 text-xs font-semibold text-white hover:bg-rose-500 transition-colors"
              >
                <Square className="h-3.5 w-3.5 fill-current" />
                Stop
              </button>
            ) : (
              <button
                type="submit"
                disabled={!input.trim()}
                className="inline-flex h-9 items-center gap-1.5 rounded-xl bg-indigo-600 px-4 text-xs font-semibold text-white shadow-sm hover:bg-indigo-500 transition-colors disabled:opacity-40"
              >
                <Send className="h-3.5 w-3.5" />
                Send
              </button>
            )}
          </div>
          <div className="mt-1.5 flex items-center justify-between px-1 text-[11px] text-slate-400">
            <span>
               Standing IntentMandate: &lt;= ${activePersona.intentMandate.maxAutoUsd} ·{' '}
              {activePersona.intentMandate.allowedTiers.join(' / ')} vendors · &lt;{' '}
              {(activePersona.intentMandate.maxBudgetUtilization * 100).toFixed(0)}% Q4 cap
            </span>
            <span>Enter to send · Shift+Enter for newline</span>
          </div>
        </form>
      </div>
    </main>
  );
};
