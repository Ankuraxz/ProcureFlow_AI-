import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChatWorkspace } from './components/ChatWorkspace';
import { PersonaSidebar } from './components/PersonaSidebar';
import { TelemetryDrawer } from './components/TelemetryDrawer';
import {
  createSession,
  DEFAULT_AGENT_ROSTER,
  FALLBACK_PERSONAS,
  fetchPersonas,
  fetchSessionMessages,
  fetchSessions,
  fetchTelemetrySpans,
  fetchTelemetrySummary,
  normalizeA2UISurface,
  normalizeAgentActivityEvent,
  registerPersonaPublicKey,
  resetDemoLedger,
  streamChatTurn,
  submitMandateDecision,
} from './lib/api';
import { getOrCreatePersonaEd25519Key } from './lib/ed25519';
import type {
  ChatMessage,
  GuidedScenario,
  Persona,
  PersonaCryptoKeyInfo,
  ProcurementSession,
  TelemetrySpan,
  TelemetrySummary,
} from './types/procureflow';

export const App: React.FC = () => {
  const [personas, setPersonas] = useState<Persona[]>(FALLBACK_PERSONAS);
  const [activeUserId, setActiveUserId] = useState<string>(FALLBACK_PERSONAS[0].userId);
  const [backendOnline, setBackendOnline] = useState<boolean>(false);
  const [cryptoKeyInfo, setCryptoKeyInfo] = useState<PersonaCryptoKeyInfo | null>(null);

  const [sessions, setSessions] = useState<ProcurementSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState<boolean>(false);

  const [telemetryDrawerOpen, setTelemetryDrawerOpen] = useState<boolean>(true);
  const [telemetryAutoPoll, setTelemetryAutoPoll] = useState<boolean>(false);
  const [isRefreshingTelemetry, setIsRefreshingTelemetry] = useState<boolean>(false);
  const [telemetrySummary, setTelemetrySummary] = useState<TelemetrySummary>({
    totalTokens: 0,
    promptTokens: 0,
    outputTokens: 0,
    cachedTokens: 0,
    estimatedCostUsd: 0,
    a2aHops: 0,
    activeSkills: [],
    perAgent: DEFAULT_AGENT_ROSTER,
    updatedAt: new Date().toISOString(),
  });
  const [telemetrySpans, setTelemetrySpans] = useState<TelemetrySpan[]>([]);

  const abortControllerRef = useRef<AbortController | null>(null);

  const activePersona = useMemo<Persona>(() => {
    return (
      personas.find((p) => p.userId === activeUserId) ??
      personas[0] ??
      FALLBACK_PERSONAS[0]
    );
  }, [personas, activeUserId]);

  const refreshPersonas = useCallback(async () => {
    const result = await fetchPersonas();
    setPersonas(result.personas);
    setBackendOnline(result.backendOnline);
    setActiveUserId((prev) =>
      result.personas.some((p) => p.userId === prev)
        ? prev
        : (result.personas[0]?.userId ?? prev),
    );
    return result.personas;
  }, []);

  const refreshTelemetry = useCallback(async (sessionId?: string | null) => {
    setIsRefreshingTelemetry(true);
    try {
      const [summary, spans] = await Promise.all([
        fetchTelemetrySummary(sessionId ?? undefined),
        fetchTelemetrySpans(sessionId ?? undefined),
      ]);
      setTelemetrySummary(summary);
      setTelemetrySpans(spans);
    } finally {
      setIsRefreshingTelemetry(false);
    }
  }, []);

  const ensureAndRegisterPersonaKey = useCallback(
    async (userId: string, forceRotate = false) => {
      try {
        const keyPair = await getOrCreatePersonaEd25519Key(userId, forceRotate);
        const registered = await registerPersonaPublicKey(userId, keyPair.publicKeyB64);
        if (registered) {
          setBackendOnline(true);
        }
        const info: PersonaCryptoKeyInfo = {
          userId,
          publicKeyB64: keyPair.publicKeyB64,
          fingerprint: keyPair.fingerprint,
          createdAt: keyPair.createdAt,
          registeredOnBackend: registered,
        };
        setCryptoKeyInfo(info);
        return info;
      } catch {
        setCryptoKeyInfo(null);
        return null;
      }
    },
    [],
  );

  // Initial load of personas + telemetry
  useEffect(() => {
    void refreshPersonas();
    void refreshTelemetry(null);
  }, [refreshPersonas, refreshTelemetry]);

  // Whenever active persona changes: load/generate Ed25519 WebCrypto key, register with backend, and load sessions
  useEffect(() => {
    let cancelled = false;
    const syncPersonaContext = async () => {
      await ensureAndRegisterPersonaKey(activeUserId, false);
      const userSessions = await fetchSessions(activeUserId);
      if (!cancelled) {
        setSessions(userSessions);
      }
    };
    void syncPersonaContext();
    return () => {
      cancelled = true;
    };
  }, [activeUserId, ensureAndRegisterPersonaKey]);

  // Optional 5s live telemetry polling
  useEffect(() => {
    if (!telemetryAutoPoll) return undefined;
    const timer = window.setInterval(() => {
      void refreshTelemetry(activeSessionId);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [telemetryAutoPoll, activeSessionId, refreshTelemetry]);

  const handleSelectPersona = (newUserId: string) => {
    if (newUserId === activeUserId || isStreaming) return;
    setActiveUserId(newUserId);
    setActiveSessionId(null);
    setMessages([]);
  };

  const handleRotateKey = async () => {
    await ensureAndRegisterPersonaKey(activeUserId, true);
  };

  const handleNewSession = useCallback(async () => {
    if (isStreaming) return;
    const created = await createSession(activeUserId);
    setActiveSessionId(created.sessionId);
    setMessages([]);
    const updatedSessions = await fetchSessions(activeUserId);
    setSessions(
      updatedSessions.some((s) => s.sessionId === created.sessionId)
        ? updatedSessions
        : [created, ...updatedSessions],
    );
  }, [activeUserId, isStreaming]);

  const handleSelectSession = useCallback(
    async (sessionId: string) => {
      if (isStreaming) return;
      setActiveSessionId(sessionId);
      const history = await fetchSessionMessages(sessionId);
      setMessages(history);
      void refreshTelemetry(sessionId);
    },
    [isStreaming, refreshTelemetry],
  );

  const executeMessageSend = useCallback(
    async (promptText: string, overrideUserId?: string) => {
      const targetUserId = overrideUserId ?? activeUserId;
      const trimmed = promptText.trim();
      if (!trimmed || isStreaming) return;

      // Ensure persona's WebCrypto Ed25519 public key is registered before turn starts
      await ensureAndRegisterPersonaKey(targetUserId, false);

      let sessionId = overrideUserId ? null : activeSessionId;
      if (!sessionId) {
        const created = await createSession(
          targetUserId,
          trimmed.length > 44 ? `${trimmed.slice(0, 44)}...` : trimmed,
        );
        sessionId = created.sessionId;
        setActiveSessionId(sessionId);
        setSessions((prev) =>
          prev.some((s) => s.sessionId === created.sessionId)
            ? prev
            : [created, ...prev],
        );
      }

      const nowIso = new Date().toISOString();
      const userMessage: ChatMessage = {
        id: `user-${Date.now()}`,
        role: 'user',
        content: trimmed,
        createdAt: nowIso,
        agentActivities: [],
        skillsActivated: [],
        surfaces: [],
      };

      const assistantMsgId = `asst-${Date.now() + 1}`;
      const initialAssistantMessage: ChatMessage = {
        id: assistantMsgId,
        role: 'assistant',
        content: '',
        createdAt: nowIso,
        isStreaming: true,
        agentActivities: [],
        skillsActivated: [],
        surfaces: [],
      };

      setMessages((prev) =>
        overrideUserId
          ? [userMessage, initialAssistantMessage]
          : [...prev, userMessage, initialAssistantMessage],
      );
      setIsStreaming(true);

      const abortController = new AbortController();
      abortControllerRef.current = abortController;

      let resolvedSessionId = sessionId;

      try {
        await streamChatTurn({
          userId: targetUserId,
          sessionId,
          message: trimmed,
          signal: abortController.signal,
          onEvent: (event) => {
            setBackendOnline(true);

            if (
              typeof event.payload.session_id === 'string' &&
              event.payload.session_id
            ) {
              resolvedSessionId = event.payload.session_id;
              setActiveSessionId(resolvedSessionId);
            }

            setMessages((prev) =>
              prev.map((m) => {
                if (m.id !== assistantMsgId) return m;

                if (event.type === 'agent_status') {
                  const pill = normalizeAgentActivityEvent(event.payload);
                  const existingIdx = m.agentActivities.findIndex(
                    (a) => a.agentName === pill.agentName,
                  );
                  const nextActivities = [...m.agentActivities];
                  if (existingIdx >= 0) {
                    nextActivities[existingIdx] = pill;
                  } else {
                    nextActivities.push(pill);
                  }
                  return { ...m, agentActivities: nextActivities };
                }

                if (event.type === 'skill_activated') {
                  const skillName = String(
                    event.payload.skill_name ??
                      event.payload.skill ??
                      event.payload.name ??
                      'procedural-skill',
                  );
                  if (m.skillsActivated.some((s) => s.skillName === skillName)) {
                    return m;
                  }
                  return {
                    ...m,
                    skillsActivated: [
                      ...m.skillsActivated,
                      {
                        id: `skill-${skillName}-${Date.now()}`,
                        skillName,
                        agentName: String(
                          event.payload.agent ??
                            event.payload.agent_name ??
                            'concierge_orchestrator',
                        ),
                        level: String(event.payload.level ?? 'L2'),
                        timestamp: new Date().toISOString(),
                      },
                    ],
                  };
                }

                if (event.type === 'text_delta') {
                  const delta = String(
                    event.payload.delta ??
                      event.payload.text ??
                      event.payload.content ??
                      '',
                  );
                  return { ...m, content: m.content + delta };
                }

                if (event.type === 'text_done') {
                  const fullText = String(
                    event.payload.text ?? event.payload.content ?? '',
                  );
                  return {
                    ...m,
                    content: fullText || m.content,
                  };
                }

                if (event.type === 'a2ui_surface') {
                  const normalizedSurface = normalizeA2UISurface(
                    event.payload,
                    m.surfaces.length,
                  );
                  if (!normalizedSurface) return m;
                  const existingIdx = m.surfaces.findIndex(
                    (s) => s.id === normalizedSurface.id,
                  );
                  const nextSurfaces = [...m.surfaces];
                  if (existingIdx >= 0) {
                    nextSurfaces[existingIdx] = normalizedSurface;
                  } else {
                    nextSurfaces.push(normalizedSurface);
                  }
                  return { ...m, surfaces: nextSurfaces };
                }

                if (event.type === 'hitl_required') {
                  const hasMandateCard = m.surfaces.some(
                    (s) => s.surfaceType === 'AP2CartMandateApprovalCard',
                  );
                  const nextSurfaces = [...m.surfaces];
                  if (!hasMandateCard) {
                    const candidateSource =
                      event.payload.cart_mandate ??
                      event.payload.mandate ??
                      event.payload;
                    if (
                      candidateSource &&
                      typeof candidateSource === 'object' &&
                      ('cart_mandate_id' in (candidateSource as Record<string, unknown>) ||
                        'mandate_id' in (candidateSource as Record<string, unknown>) ||
                        'cart_sha256' in (candidateSource as Record<string, unknown>))
                    ) {
                      const synthesized = normalizeA2UISurface(
                        {
                          surface_type: 'AP2CartMandateApprovalCard',
                          payload: candidateSource,
                        },
                        nextSurfaces.length,
                      );
                      if (synthesized) {
                        nextSurfaces.push(synthesized);
                      }
                    }
                  }

                  const nextActivities = m.agentActivities.map((act) =>
                    act.agentName === 'ap2_settlement_agent'
                      ? { ...act, status: 'waiting_hitl' as const }
                      : act,
                  );

                  return {
                    ...m,
                    hitlRequired: true,
                    surfaces: nextSurfaces,
                    agentActivities: nextActivities,
                  };
                }

                if (event.type === 'error') {
                  const errMsg = String(
                    event.payload.message ??
                      event.payload.detail ??
                      event.payload.error ??
                      'Unexpected stream error from Gateway.',
                  );
                  return {
                    ...m,
                    isStreaming: false,
                    error: errMsg,
                  };
                }

                if (event.type === 'done') {
                  return {
                    ...m,
                    isStreaming: false,
                    agentActivities: m.agentActivities.map((a) =>
                      a.status === 'running' ? { ...a, status: 'completed' } : a,
                    ),
                  };
                }

                return m;
              }),
            );
          },
        });
      } catch (err) {
        if ((err as Error)?.name !== 'AbortError') {
          const messageText =
            err instanceof Error
              ? err.message
              : 'Unable to reach ProcureFlow Gateway on http://localhost:8000.';
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMsgId
                ? {
                    ...m,
                    isStreaming: false,
                    error: `${messageText} — Verify FastAPI Gateway (:8000) and A2A services (:8001–8003) are running.`,
                  }
                : m,
            ),
          );
        }
      } finally {
        setIsStreaming(false);
        abortControllerRef.current = null;
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMsgId
              ? {
                  ...m,
                  isStreaming: false,
                  agentActivities: m.agentActivities.map((a) =>
                    a.status === 'running' ? { ...a, status: 'completed' } : a,
                  ),
                }
              : m,
          ),
        );

        const [, updatedSessions] = await Promise.all([
          refreshPersonas(),
          fetchSessions(targetUserId),
          refreshTelemetry(resolvedSessionId),
        ]);
        if (updatedSessions.length > 0) {
          setSessions(updatedSessions);
        }
      }
    },
    [
      activeSessionId,
      activeUserId,
      ensureAndRegisterPersonaKey,
      isStreaming,
      refreshPersonas,
      refreshTelemetry,
    ],
  );

  const handleStopStreaming = useCallback(() => {
    abortControllerRef.current?.abort();
    setIsStreaming(false);
  }, []);

  const handleRunScenario = useCallback(
    async (scenario: GuidedScenario) => {
      if (isStreaming) return;
      let targetUserId = activeUserId;
      if (scenario.suggestedDepartmentHint) {
        const hintLower = scenario.suggestedDepartmentHint.toLowerCase();
        if (!activePersona.departmentName.toLowerCase().includes(hintLower)) {
          const matchedPersona = personas.find((p) =>
            p.departmentName.toLowerCase().includes(hintLower),
          );
          if (matchedPersona) {
            targetUserId = matchedPersona.userId;
            setActiveUserId(matchedPersona.userId);
            setActiveSessionId(null);
          }
        }
      }
      await executeMessageSend(scenario.prompt, targetUserId !== activeUserId ? targetUserId : undefined);
    },
    [activePersona.departmentName, activeUserId, executeMessageSend, isStreaming, personas],
  );

  const handleMandateDecision = useCallback(
    async (params: {
      cartMandateId: string;
      decision: 'approve' | 'reject';
      signatureB64?: string;
      publicKeyB64?: string;
      canonicalPayload?: string;
      reason?: string;
    }) => {
      const sessionId = activeSessionId ?? 'default-session';
      const result = await submitMandateDecision({
        cartMandateId: params.cartMandateId,
        userId: activeUserId,
        sessionId,
        decision: params.decision,
        signatureB64: params.signatureB64,
        publicKeyB64: params.publicKeyB64,
        canonicalPayload: params.canonicalPayload,
        reason: params.reason,
      });

      // Update the CartMandate card status in existing messages and append settlement turn
      setMessages((prev) => {
        const updated = prev.map((msg) => ({
          ...msg,
          agentActivities: msg.agentActivities.map((act) =>
            act.status === 'waiting_hitl' ? { ...act, status: 'completed' as const } : act,
          ),
          surfaces: msg.surfaces.map((surf) => {
            if (
              surf.surfaceType === 'AP2CartMandateApprovalCard' &&
              surf.data.cartMandateId === params.cartMandateId
            ) {
              return {
                ...surf,
                data: {
                  ...surf.data,
                  status:
                    params.decision === 'approve'
                      ? ('SIGNED_AND_SETTLED' as const)
                      : ('REJECTED' as const),
                  signatureB64: params.signatureB64 ?? surf.data.signatureB64,
                  signerPublicKeyB64:
                    params.publicKeyB64 ?? surf.data.signerPublicKeyB64,
                },
              };
            }
            return surf;
          }),
        }));

        const followUpMessage: ChatMessage = {
          id: `mandate-res-${Date.now()}`,
          role: 'assistant',
          content: result.message,
          createdAt: new Date().toISOString(),
          agentActivities: [
            {
              id: `settle-${Date.now()}`,
              agentName: 'ap2_settlement_agent',
              displayName: 'AP2 Settlement SLM',
              port: 8003,
              status: 'completed',
              detail:
                params.decision === 'approve'
                  ? 'Ed25519 CartMandate verified · PaymentMandate committed'
                  : 'CartMandate rejected',
              timestamp: new Date().toISOString(),
            },
          ],
          skillsActivated: [],
          surfaces: result.surfaces,
        };

        return [...updated, followUpMessage];
      });

      await Promise.all([
        refreshPersonas(),
        refreshTelemetry(activeSessionId),
      ]);
    },
    [activeSessionId, activeUserId, refreshPersonas, refreshTelemetry],
  );

  const handleResetLedger = useCallback(async () => {
    await resetDemoLedger();
    setMessages([]);
    setActiveSessionId(null);
    const freshPersonas = await refreshPersonas();
    const targetUser =
      freshPersonas.find((p) => p.userId === activeUserId)?.userId ??
      freshPersonas[0]?.userId ??
      activeUserId;
    await ensureAndRegisterPersonaKey(targetUser, false);
    const freshSessions = await fetchSessions(targetUser);
    setSessions(freshSessions);
    await refreshTelemetry(null);
  }, [activeUserId, ensureAndRegisterPersonaKey, refreshPersonas, refreshTelemetry]);

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-slate-100">
      {/* Left Pane: Persona, Budget, Ed25519 Signer, Sessions & 1-Click Demo Scenarios */}
      <PersonaSidebar
        personas={personas}
        activePersona={activePersona}
        cryptoKeyInfo={cryptoKeyInfo}
        backendOnline={backendOnline}
        sessions={sessions}
        activeSessionId={activeSessionId}
        isStreaming={isStreaming}
        onSelectPersona={handleSelectPersona}
        onRotateKey={handleRotateKey}
        onResetLedger={handleResetLedger}
        onNewSession={handleNewSession}
        onSelectSession={handleSelectSession}
        onRunScenario={handleRunScenario}
      />

      {/* Center Pane: Conversational + Declarative A2UI Stream */}
      <ChatWorkspace
        activePersona={activePersona}
        cryptoKeyInfo={cryptoKeyInfo}
        activeSessionId={activeSessionId}
        messages={messages}
        isStreaming={isStreaming}
        telemetryDrawerOpen={telemetryDrawerOpen}
        totalTokensBadge={telemetrySummary.totalTokens}
        onToggleTelemetryDrawer={() => setTelemetryDrawerOpen((prev) => !prev)}
        onSendMessage={(text) => executeMessageSend(text)}
        onStopStreaming={handleStopStreaming}
        onRunScenario={handleRunScenario}
        onMandateDecision={handleMandateDecision}
      />

      {/* Right Collapsible Pane: Live Observability, SkillToolset & Token FinOps Waterfall */}
      <TelemetryDrawer
        isOpen={telemetryDrawerOpen}
        summary={telemetrySummary}
        spans={telemetrySpans}
        isRefreshing={isRefreshingTelemetry}
        autoPoll={telemetryAutoPoll}
        onToggleAutoPoll={() => setTelemetryAutoPoll((prev) => !prev)}
        onRefresh={() => refreshTelemetry(activeSessionId)}
        onClose={() => setTelemetryDrawerOpen(false)}
      />
    </div>
  );
};

export default App;
