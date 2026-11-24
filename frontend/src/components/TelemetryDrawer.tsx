import React, { useMemo, useState } from 'react';
import {
  Activity,
  BookOpen,
  ChevronDown,
  ChevronRight,
  Clock,
  Coins,
  Cpu,
  Layers,
  Network,
  RefreshCw,
  X,
} from 'lucide-react';
import type { TelemetrySpan, TelemetrySummary } from '../types/procureflow';

interface TelemetryDrawerProps {
  isOpen: boolean;
  summary: TelemetrySummary;
  spans: TelemetrySpan[];
  isRefreshing: boolean;
  autoPoll: boolean;
  onToggleAutoPoll: () => void;
  onRefresh: () => Promise<void>;
  onClose: () => void;
}

interface WaterfallNode {
  span: TelemetrySpan;
  depth: number;
  offsetPct: number;
  widthPct: number;
}

function getPortColor(port: number): {
  bar: string;
  badge: string;
} {
  switch (port) {
    case 8001:
      return {
        bar: 'bg-sky-500',
        badge: 'bg-sky-500/15 text-sky-300 border-sky-500/30',
      };
    case 8002:
      return {
        bar: 'bg-amber-500',
        badge: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
      };
    case 8003:
      return {
        bar: 'bg-emerald-500',
        badge: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
      };
    default:
      return {
        bar: 'bg-indigo-500',
        badge: 'bg-indigo-500/15 text-indigo-300 border-indigo-500/30',
      };
  }
}

function buildTraceWaterfall(spans: TelemetrySpan[]): WaterfallNode[] {
  if (spans.length === 0) return [];

  const sorted = [...spans].sort((a, b) => a.startTimeMs - b.startTimeMs);
  const minStart = Math.min(...sorted.map((s) => s.startTimeMs));
  const maxEnd = Math.max(...sorted.map((s) => s.endTimeMs));
  const totalWindow = Math.max(1, maxEnd - minStart);

  const byId = new Map<string, TelemetrySpan>();
  const childrenMap = new Map<string, TelemetrySpan[]>();
  for (const sp of sorted) {
    byId.set(sp.spanId, sp);
  }

  const roots: TelemetrySpan[] = [];
  for (const sp of sorted) {
    if (sp.parentSpanId && byId.has(sp.parentSpanId)) {
      const arr = childrenMap.get(sp.parentSpanId) || [];
      arr.push(sp);
      childrenMap.set(sp.parentSpanId, arr);
    } else {
      roots.push(sp);
    }
  }

  const ordered: WaterfallNode[] = [];
  const visited = new Set<string>();

  const walk = (span: TelemetrySpan, depth: number) => {
    if (visited.has(span.spanId)) return;
    visited.add(span.spanId);

    const rawOffset = ((span.startTimeMs - minStart) / totalWindow) * 100;
    const rawWidth = (span.durationMs / totalWindow) * 100;
    const offsetPct = Math.min(96, Math.max(0, rawOffset));
    const widthPct = Math.min(100 - offsetPct, Math.max(4, rawWidth));

    ordered.push({
      span,
      depth: Math.min(5, depth),
      offsetPct,
      widthPct,
    });

    const kids = childrenMap.get(span.spanId) || [];
    kids.sort((a, b) => a.startTimeMs - b.startTimeMs);
    for (const child of kids) {
      walk(child, depth + 1);
    }
  };

  for (const root of roots) {
    walk(root, 0);
  }
  for (const sp of sorted) {
    if (!visited.has(sp.spanId)) {
      walk(sp, 0);
    }
  }

  return ordered;
}

export const TelemetryDrawer: React.FC<TelemetryDrawerProps> = ({
  isOpen,
  summary,
  spans,
  isRefreshing,
  autoPoll,
  onToggleAutoPoll,
  onRefresh,
  onClose,
}) => {
  const [expandedSpanIds, setExpandedSpanIds] = useState<Set<string>>(new Set());
  const [selectedTraceId, setSelectedTraceId] = useState<string>('ALL');

  const traceIds = useMemo(() => {
    const unique = Array.from(new Set(spans.map((s) => s.traceId)));
    return unique;
  }, [spans]);

  const filteredSpans = useMemo(() => {
    if (selectedTraceId === 'ALL' || !traceIds.includes(selectedTraceId)) {
      // Default to latest trace if available so waterfall timeline is cohesive
      if (traceIds.length > 0 && selectedTraceId === 'ALL') {
        const latestTrace = spans[spans.length - 1]?.traceId;
        if (latestTrace) {
          return spans.filter((s) => s.traceId === latestTrace);
        }
      }
      return spans;
    }
    return spans.filter((s) => s.traceId === selectedTraceId);
  }, [spans, selectedTraceId, traceIds]);

  const waterfallNodes = useMemo(
    () => buildTraceWaterfall(filteredSpans),
    [filteredSpans],
  );

  if (!isOpen) {
    return null;
  }

  const toggleSpan = (spanId: string) => {
    setExpandedSpanIds((prev) => {
      const next = new Set(prev);
      if (next.has(spanId)) {
        next.delete(spanId);
      } else {
        next.add(spanId);
      }
      return next;
    });
  };

  const maxAgentTokens = Math.max(
    1,
    ...summary.perAgent.map((a) => a.totalTokens),
  );

  return (
    <aside className="flex h-full w-96 shrink-0 flex-col border-l border-slate-800 bg-slate-950 text-slate-100">
      {/* Drawer Header */}
      <div className="flex h-14 shrink-0 items-center justify-between border-b border-slate-800 px-4">
        <div className="flex items-center gap-2">
          <Activity className="h-4 w-4 text-indigo-400" />
          <div>
            <h3 className="text-xs font-bold uppercase tracking-wider text-white">
              Observability &amp; Token FinOps
            </h3>
            <p className="text-[10px] text-slate-400">
              ADK Callbacks · W3C A2A OpenTelemetry
            </p>
          </div>
        </div>

        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={onToggleAutoPoll}
            title="Toggle 5s Live Polling"
            className={`rounded-md border px-2 py-1 text-[10px] font-semibold transition-colors ${
              autoPoll
                ? 'border-emerald-500/40 bg-emerald-500/15 text-emerald-300'
                : 'border-slate-700 bg-slate-900 text-slate-400 hover:text-slate-200'
            }`}
          >
            {autoPoll ? 'Auto ON' : 'Auto OFF'}
          </button>

          <button
            type="button"
            onClick={() => void onRefresh()}
            title="Refresh Telemetry & Spans"
            className="rounded-md border border-slate-700 bg-slate-900 p-1.5 text-slate-300 hover:bg-slate-800 hover:text-white transition-colors"
          >
            <RefreshCw
              className={`h-3.5 w-3.5 ${isRefreshing ? 'animate-spin text-indigo-400' : ''}`}
            />
          </button>

          <button
            type="button"
            onClick={onClose}
            title="Collapse Drawer"
            className="rounded-md p-1.5 text-slate-400 hover:bg-slate-800 hover:text-white transition-colors"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Scrollable Telemetry Body */}
      <div className="dark-scrollbar flex-1 overflow-y-auto p-3.5 space-y-4">
        {/* Top KPI Grid */}
        <section className="grid grid-cols-2 gap-2.5">
          <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-3">
            <div className="flex items-center justify-between text-[10px] font-semibold uppercase tracking-wider text-slate-400">
              <span>Total Tokens</span>
              <Layers className="h-3.5 w-3.5 text-indigo-400" />
            </div>
            <div className="mt-1 font-mono text-lg font-bold text-white">
              {summary.totalTokens.toLocaleString()}
            </div>
            <div className="mt-1 flex items-center gap-1.5 font-mono text-[10px] text-slate-400">
              <span title="Prompt Tokens">P:{summary.promptTokens}</span>
              <span>·</span>
              <span title="Output Candidate Tokens">O:{summary.outputTokens}</span>
              <span>·</span>
              <span title="Cached Context Tokens" className="text-emerald-400">
                C:{summary.cachedTokens}
              </span>
            </div>
          </div>

          <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-3">
            <div className="flex items-center justify-between text-[10px] font-semibold uppercase tracking-wider text-slate-400">
              <span>Est. FinOps Cost</span>
              <Coins className="h-3.5 w-3.5 text-emerald-400" />
            </div>
            <div className="mt-1 font-mono text-lg font-bold text-emerald-300">
              ${summary.estimatedCostUsd.toFixed(6)}
            </div>
            <div className="mt-1 text-[10px] text-slate-400">
              Flash + Flash-Lite Tiering
            </div>
          </div>

          <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-3">
            <div className="flex items-center justify-between text-[10px] font-semibold uppercase tracking-wider text-slate-400">
              <span>A2A Mesh Hops</span>
              <Network className="h-3.5 w-3.5 text-sky-400" />
            </div>
            <div className="mt-1 font-mono text-lg font-bold text-sky-300">
              {summary.a2aHops}
            </div>
            <div className="mt-1 text-[10px] text-slate-400">
              JSON-RPC + W3C traceparent
            </div>
          </div>

          <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-3">
            <div className="flex items-center justify-between text-[10px] font-semibold uppercase tracking-wider text-slate-400">
              <span>Skills Loaded</span>
              <BookOpen className="h-3.5 w-3.5 text-violet-400" />
            </div>
            <div className="mt-1 font-mono text-lg font-bold text-violet-300">
              {summary.activeSkills.length}
            </div>
            <div className="mt-1 text-[10px] text-slate-400">
              Lazy L1 → L2 SkillToolset
            </div>
          </div>
        </section>

        {/* Active Procedural Skills (SkillToolset Badges) */}
        <section className="rounded-xl border border-slate-800 bg-slate-900/60 p-3 space-y-2">
          <div className="flex items-center justify-between">
            <span className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-300">
              <BookOpen className="h-3.5 w-3.5 text-violet-400" />
              Active Procedural Skills (SkillToolset)
            </span>
          </div>
          {summary.activeSkills.length === 0 ? (
            <p className="text-[11px] text-slate-500">
              L1 metadata armed (~80 tok/skill). Full L2 SKILL.md playbooks load on
              agent demand.
            </p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {summary.activeSkills.map((skill) => (
                <span
                  key={skill}
                  className="inline-flex items-center gap-1 rounded-md border border-violet-500/30 bg-violet-500/15 px-2 py-0.5 font-mono text-[10px] font-medium text-violet-200"
                >
                  {skill}
                </span>
              ))}
            </div>
          )}
        </section>

        {/* Per-Agent Token & Latency Breakdown */}
        <section className="rounded-xl border border-slate-800 bg-slate-900/60 p-3 space-y-2.5">
          <div className="flex items-center justify-between">
            <span className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-300">
              <Cpu className="h-3.5 w-3.5 text-indigo-400" />
              Per-Agent Token &amp; Latency Fleet
            </span>
          </div>

          <div className="space-y-2.5">
            {summary.perAgent.map((agent) => {
              const pct = Math.min(
                100,
                Math.round((agent.totalTokens / maxAgentTokens) * 100),
              );
              const colors = getPortColor(agent.port);
              return (
                <div
                  key={agent.agentName}
                  className="rounded-lg border border-slate-800/90 bg-slate-950/70 p-2.5 space-y-1.5"
                >
                  <div className="flex items-center justify-between gap-1.5">
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5">
                        <span className="truncate text-xs font-semibold text-slate-100">
                          {agent.displayName}
                        </span>
                        <span
                          className={`rounded border px-1.5 py-0.2 font-mono text-[9px] font-semibold ${colors.badge}`}
                        >
                          :{agent.port}
                        </span>
                      </div>
                      <span className="block font-mono text-[10px] text-slate-400">
                        {agent.modelName}
                      </span>
                    </div>

                    <div className="text-right shrink-0">
                      <span className="block font-mono text-xs font-bold text-white">
                        {agent.totalTokens.toLocaleString()} tok
                      </span>
                      <span className="block font-mono text-[10px] text-emerald-400">
                        ${agent.estimatedCostUsd.toFixed(6)}
                      </span>
                    </div>
                  </div>

                  <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
                    <div
                      className={`h-full rounded-full transition-all duration-500 ${colors.bar}`}
                      style={{ width: `${agent.totalTokens > 0 ? Math.max(4, pct) : 0}%` }}
                    />
                  </div>

                  <div className="flex items-center justify-between font-mono text-[10px] text-slate-400">
                    <span>
                      P:{agent.promptTokens} · O:{agent.outputTokens} · C:
                      {agent.cachedTokens}
                    </span>
                    <span className="flex items-center gap-1 text-slate-300">
                      <Clock className="h-2.5 w-2.5 text-slate-500" />
                      {agent.avgLatencyMs > 0
                        ? `${Math.round(agent.avgLatencyMs)} ms`
                        : 'idle'}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        {/* Distributed Trace Waterfall */}
        <section className="rounded-xl border border-slate-800 bg-slate-900/60 p-3 space-y-2.5">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-300">
              Distributed Trace Waterfall
            </span>
            {traceIds.length > 1 && (
              <select
                aria-label="Filter Trace ID"
                value={selectedTraceId}
                onChange={(e) => setSelectedTraceId(e.target.value)}
                className="rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-200"
              >
                <option value="ALL">Latest Trace</option>
                {traceIds.map((tid) => (
                  <option key={tid} value={tid}>
                    {tid.slice(0, 12)}...
                  </option>
                ))}
              </select>
            )}
          </div>

          {waterfallNodes.length === 0 ? (
            <div className="rounded-lg border border-dashed border-slate-800 p-4 text-center text-[11px] text-slate-500">
              Run a procurement query to inspect live W3C{' '}
              <code className="font-mono text-slate-400">traceparent</code> spans
              across ports <code className="font-mono text-slate-400">8000–8003</code>.
            </div>
          ) : (
            <div className="space-y-1.5">
              <div className="flex items-center justify-between px-1 font-mono text-[10px] text-slate-500">
                <span>
                  trace_id: {waterfallNodes[0].span.traceId.slice(0, 16)}...
                </span>
                <span>{waterfallNodes.length} spans</span>
              </div>

              {waterfallNodes.map(({ span, depth, offsetPct, widthPct }) => {
                const isExpanded = expandedSpanIds.has(span.spanId);
                const colors = getPortColor(span.port);

                return (
                  <div
                    key={span.spanId}
                    className="rounded-lg border border-slate-800/90 bg-slate-950/80 p-2 transition-colors hover:border-slate-700"
                  >
                    <button
                      type="button"
                      onClick={() => toggleSpan(span.spanId)}
                      className="w-full text-left space-y-1.5"
                    >
                      <div
                        className="flex items-center justify-between gap-1.5"
                        style={{ paddingLeft: `${depth * 10}px` }}
                      >
                        <div className="flex items-center gap-1 min-w-0">
                          {isExpanded ? (
                            <ChevronDown className="h-3 w-3 shrink-0 text-slate-400" />
                          ) : (
                            <ChevronRight className="h-3 w-3 shrink-0 text-slate-400" />
                          )}
                          <span
                            className={`shrink-0 rounded border px-1 py-0.2 font-mono text-[9px] ${colors.badge}`}
                          >
                            :{span.port}
                          </span>
                          <span className="truncate font-mono text-[11px] font-medium text-slate-200">
                            {span.name}
                          </span>
                        </div>
                        <span
                          className={`shrink-0 font-mono text-[10px] ${
                            span.status === 'ERROR'
                              ? 'text-rose-400 font-semibold'
                              : 'text-slate-400'
                          }`}
                        >
                          {span.durationMs}ms
                        </span>
                      </div>

                      {/* Waterfall Timeline Bar */}
                      <div className="relative h-1.5 w-full overflow-hidden rounded-full bg-slate-900">
                        <div
                          className={`absolute top-0 bottom-0 rounded-full ${
                            span.status === 'ERROR' ? 'bg-rose-500' : colors.bar
                          }`}
                          style={{
                            left: `${offsetPct}%`,
                            width: `${widthPct}%`,
                          }}
                        />
                      </div>
                    </button>

                    {isExpanded && (
                      <div className="mt-2 space-y-1 border-t border-slate-800/80 pt-2 font-mono text-[10px] text-slate-300">
                        <div className="flex justify-between text-slate-400">
                          <span>trace_id:</span>
                          <span className="text-slate-200">{span.traceId}</span>
                        </div>
                        <div className="flex justify-between text-slate-400">
                          <span>span_id:</span>
                          <span className="text-slate-200">{span.spanId}</span>
                        </div>
                        {span.parentSpanId && (
                          <div className="flex justify-between text-slate-400">
                            <span>parent_id:</span>
                            <span className="text-slate-200">{span.parentSpanId}</span>
                          </div>
                        )}
                        <div className="flex justify-between text-slate-400">
                          <span>service:</span>
                          <span className="text-indigo-300">
                            {span.serviceName} (:{span.port})
                          </span>
                        </div>
                        {Object.keys(span.attributes).length > 0 && (
                          <pre className="mt-1 max-h-36 overflow-auto rounded bg-slate-900 p-2 text-[10px] text-emerald-200 border border-slate-800">
                            {JSON.stringify(span.attributes, null, 2)}
                          </pre>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </section>
      </div>
    </aside>
  );
};
