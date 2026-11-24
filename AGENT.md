# ProcureFlow AI — Agentic Architecture & Developer Guide (`AGENT.md`)

**ProcureFlow AI** is a local-first, production-patterned **Autonomous Enterprise Procurement & Spend Desk** built for the Agentic AI showcase using **Google ADK**, **Gemini**, **A2A**, **A2UI**, and **AP2 (Agent Payments Protocol)**.

---

## 1. Core Architectural Pillars

1. **Google ADK Multi-Agent + A2A Topology**:
   - **Root Orchestrator (`concierge_orchestrator` — `gemini-3.5-flash`)**: Manages multi-turn conversation, session state, HITL pauses, and A2UI surface composition. Communicates with remote domain agents over **A2A HTTP/JSON-RPC** (`RemoteA2aAgent` <-> `to_a2a()` + `/.well-known/agent.json` `AgentCard`).
   - **Specialized Task SLMs (`gemini-3.5-flash-lite` / `gemini-3.5-flash`)**: Three independent A2A domain agents, each scoped to a single bounded domain with dedicated custom Python tools (no 3rd-party MCP servers):
     - `catalog_agent` (Port `8001`): Hardware/SaaS SKU discovery, bulk-tier discount math, and warehouse lead-time ranking.
     - `policy_risk_agent` (Port `8002`): Department budget ledger checks, vendor allowlist compliance, and HITL escalation scoring.
     - `ap2_settlement_agent` (Port `8003`): Cryptographic **AP2 Mandate** lifecycle (`IntentMandate` -> `CartMandate` -> `PaymentMandate` using Ed25519 signatures) and mock ledger settlement.

2. **Four-Tier Local Memory Architecture**:
   - **Procedural Memory (`skills/` folder)**: Markdown `SKILL.md` Standard Operating Procedures loaded dynamically via Google ADK's `SkillToolset` (`load_skill_from_dir`) using progressive disclosure (L1 metadata -> L2 instructions -> L3 references) so specialized SLMs stay lean and token-efficient.
   - **Working / Session Memory (`data/procureflow_sessions.db`)**: ADK `DatabaseSessionService` (SQLite) tracking multi-turn conversation history, scoped state (`temp:`, `user:`, `app:`), and HITL confirmation state.
   - **Episodic Memory (`data/procureflow_domain.db` -> `episodic_memories`)**: Past user procurement episodes, approval outcomes, and historical preferences.
   - **Semantic / Domain Store (`data/procureflow_domain.db`)**: Seeded SQLite relational tables (`catalog_skus`, `vendors`, `department_budgets`, `ap2_mandates`, `orders`).

3. **A2UI + Conditional HITL + AP2 Protocol**:
   - **A2UI Declarative Surfaces**: Backend emits typed JSON widget payloads rendered natively by the **React + Vite + Tailwind** client (`VendorComparisonCard`, `BudgetGaugeCard`, `AP2CartMandateApprovalCard`, `OrderReceiptCard`).
   - **Autonomous Path (`<= $500`, allowlisted vendor, `<80%` budget util)**: Authorized automatically against a pre-signed user `IntentMandate`.
   - **HITL Escalation Path (`> $500`, unverified vendor, or `>=80%` budget util)**: Halts execution, renders an interactive `AP2CartMandateApprovalCard` with SHA-256 cart digest and policy warnings, and requires human approval + Ed25519 `CartMandate` signature before `PaymentMandate` settlement.

4. **Tracing, Token & Cost Observability**:
   - **ADK Lifecycle Callbacks** (`before_model_callback`, `after_model_callback`, `before_tool_callback`, `after_tool_callback`) + **OpenTelemetry**:
     - Records prompt/candidate/cached tokens, per-model USD cost estimates, tool arguments & execution ms, `SkillToolset` activations, and W3C `traceparent` spans across A2A hops.
     - Persisted locally to SQLite (`telemetry_spans`, `token_usage_ledger`) for the live **UI Observability & FinOps Drawer**, with optional `CloudTraceSpanExporter` in Cloud Run.

5. **Evaluation Suite (`evals/`) — Trajectory + LLM-as-a-Judge**:
   - Combines ADK deterministic trajectory checks (`tool_trajectory_avg_score` & schema invariants) with an **LLM-as-a-Judge** harness evaluating atomic boolean rubrics (Policy Faithfulness, AP2 HITL Safety, Procedural Skill Adherence, and A2UI Completeness).

6. **Cloud Run Infra & CI/CD Reference Code**:
   - Multi-service `Dockerfile`s, `docker-compose.yml`, Terraform Cloud Run v2 IaC (`infra/terraform/`), and CI/CD pipelines (`.github/workflows/ci-cd.yml` & `cloudbuild.yaml`) with automated `pytest` + LLM-as-a-Judge eval gates.

---

## 2. Environment & Tooling Rules (`uv` + Node.js)

- **Python Interpreter**: Managed via `uv` (`Python 3.12+`, `.venv/`). `pyproject.toml` configures `python-preference = "only-system"` and `python-downloads = "never"` for reproducible local and container environments.
- Run Python commands via `.venv/bin/python`, `.venv/bin/pytest`, or `uv run ...`.
- **Node.js / npm (Frontend)**: Node.js `22+` (`nvm` or system `PATH`) for building and running the `frontend/` Vite workspace.
- **Architecture Reference**: See [`Architecture.md`](./Architecture.md) for all Architecture Decision Records (ADRs) and protocol diagrams.

---

## 3. Repository Layout

```text
root/
├── AGENT.md                          # Architecture & developer rules
├── pyproject.toml                    # uv dependencies & project config
├── skills/                           # Procedural Memory (SLM SKILL.md playbooks)
│   ├── hardware-sourcing-sop/SKILL.md
│   ├── procurement-policy-audit/SKILL.md
│   ├── ap2-mandate-lifecycle/SKILL.md
│   └── a2ui-surface-composer/SKILL.md
├── src/procureflow_ai/
│   ├── config.py                     # Env, model tiers, ports, thresholds
│   ├── db/                           # SQLite schema, seed data & async queries
│   ├── protocols/                    # AP2 cryptographic mandates & A2UI schemas
│   ├── observability/                # OTel tracer + ADK token/tool callbacks
│   ├── tools/                        # Pure-Python custom ADK tools per agent
│   ├── agents/                       # ADK orchestrator + 3 A2A SLM sub-agents
│   └── api/                          # FastAPI Gateway (SSE/A2UI/HITL/Telemetry) + A2A servers
├── frontend/                         # React + Vite + Tailwind A2UI Client & Trace Drawer
├── evals/                            # Golden datasets, trajectory tests & LLM-as-a-Judge
├── infra/terraform/                  # Cloud Run multi-service IaC (demo/reference)
├── docker/                           # Multi-stage Dockerfiles & docker-compose.yml
├── .github/workflows/ci-cd.yml       # GitHub Actions CI/CD with eval gate
└── cloudbuild.yaml                   # Google Cloud Build CI/CD pipeline
```
