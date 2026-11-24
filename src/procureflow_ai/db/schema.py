"""Relational SQLite DDL for ProcureFlow AI domain, memory, AP2 mandates, and telemetry."""

from __future__ import annotations

SCHEMA_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=10000;

CREATE TABLE IF NOT EXISTS vendors (
    vendor_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    tier TEXT NOT NULL CHECK (tier IN ('PREFERRED', 'APPROVED', 'UNVERIFIED', 'BLOCKED')),
    category TEXT NOT NULL,
    sso_ready INTEGER NOT NULL DEFAULT 1,
    default_lead_time_days INTEGER NOT NULL DEFAULT 3,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS catalog_skus (
    sku_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    vendor_id TEXT NOT NULL REFERENCES vendors(vendor_id),
    unit_price_usd REAL NOT NULL,
    stock_qty INTEGER NOT NULL DEFAULT 50,
    lead_time_days INTEGER NOT NULL DEFAULT 3,
    specs TEXT NOT NULL DEFAULT '',
    recommended_roles TEXT NOT NULL DEFAULT '',
    billing_cadence TEXT NOT NULL DEFAULT 'one_time',
    sso_supported INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS departments (
    department_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    budget_total_usd REAL NOT NULL,
    spent_usd REAL NOT NULL DEFAULT 0.0,
    currency TEXT NOT NULL DEFAULT 'USD'
);

CREATE TABLE IF NOT EXISTS personas (
    user_id TEXT PRIMARY KEY,
    full_name TEXT NOT NULL,
    email TEXT NOT NULL,
    role TEXT NOT NULL,
    department_id TEXT NOT NULL REFERENCES departments(department_id),
    public_key_b64 TEXT NOT NULL,
    default_private_key_b64 TEXT NOT NULL,
    avatar_initials TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS episodic_memories (
    episode_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES personas(user_id),
    category TEXT NOT NULL DEFAULT 'preference',
    summary TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_episodic_user ON episodic_memories(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS ap2_intent_mandates (
    intent_mandate_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES personas(user_id),
    department_id TEXT NOT NULL REFERENCES departments(department_id),
    max_auto_usd REAL NOT NULL DEFAULT 500.0,
    allowed_tiers_json TEXT NOT NULL,
    max_budget_utilization REAL NOT NULL DEFAULT 0.80,
    currency TEXT NOT NULL DEFAULT 'USD',
    canonical_payload TEXT NOT NULL,
    public_key_b64 TEXT NOT NULL,
    signature_b64 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_intent_user_status ON ap2_intent_mandates(user_id, status);

CREATE TABLE IF NOT EXISTS ap2_cart_mandates (
    cart_mandate_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL REFERENCES personas(user_id),
    department_id TEXT NOT NULL REFERENCES departments(department_id),
    vendor_id TEXT NOT NULL REFERENCES vendors(vendor_id),
    vendor_name TEXT NOT NULL,
    vendor_tier TEXT NOT NULL,
    line_items_json TEXT NOT NULL,
    total_usd REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    cart_sha256 TEXT NOT NULL,
    canonical_payload_to_sign TEXT NOT NULL,
    escalation_reasons_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'PENDING_SIGNATURE' CHECK (status IN ('PENDING_SIGNATURE', 'SIGNED_SETTLED', 'REJECTED')),
    public_key_b64 TEXT,
    signature_b64 TEXT,
    rejection_reason TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    decided_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_cart_mandate_user ON ap2_cart_mandates(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cart_mandate_session ON ap2_cart_mandates(session_id);

CREATE TABLE IF NOT EXISTS ap2_payment_mandates (
    payment_mandate_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    authorization_mode TEXT NOT NULL CHECK (authorization_mode IN ('AUTO_INTENT_MANDATE', 'HITL_CART_MANDATE')),
    intent_mandate_id TEXT,
    cart_mandate_id TEXT,
    user_id TEXT NOT NULL REFERENCES personas(user_id),
    department_id TEXT NOT NULL REFERENCES departments(department_id),
    vendor_id TEXT NOT NULL REFERENCES vendors(vendor_id),
    total_usd REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    cart_sha256 TEXT NOT NULL,
    settlement_digest_sha256 TEXT NOT NULL,
    signer_public_key_b64 TEXT NOT NULL,
    source_signature_b64 TEXT NOT NULL,
    settlement_signature_b64 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'SETTLED',
    settled_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL REFERENCES personas(user_id),
    department_id TEXT NOT NULL REFERENCES departments(department_id),
    vendor_id TEXT NOT NULL REFERENCES vendors(vendor_id),
    vendor_name TEXT NOT NULL,
    authorization_mode TEXT NOT NULL CHECK (authorization_mode IN ('AUTO_INTENT_MANDATE', 'HITL_CART_MANDATE')),
    intent_mandate_id TEXT,
    cart_mandate_id TEXT,
    payment_mandate_id TEXT NOT NULL REFERENCES ap2_payment_mandates(payment_mandate_id),
    line_items_json TEXT NOT NULL,
    total_usd REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    cart_sha256 TEXT NOT NULL,
    signature_preview TEXT NOT NULL,
    estimated_delivery_days INTEGER NOT NULL DEFAULT 3,
    status TEXT NOT NULL DEFAULT 'SETTLED',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS session_ui_turns (
    turn_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL DEFAULT '',
    surfaces_json TEXT NOT NULL DEFAULT '[]',
    hitl_mandate_json TEXT,
    trace_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ui_turns_session ON session_ui_turns(session_id, created_at ASC);

CREATE TABLE IF NOT EXISTS telemetry_spans (
    span_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    parent_span_id TEXT,
    session_id TEXT NOT NULL DEFAULT '',
    service_name TEXT NOT NULL,
    agent_name TEXT NOT NULL DEFAULT '',
    span_kind TEXT NOT NULL DEFAULT 'INTERNAL',
    name TEXT NOT NULL,
    start_time_ms REAL NOT NULL,
    duration_ms REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'OK',
    attributes_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_spans_session ON telemetry_spans(session_id, start_time_ms ASC);
CREATE INDEX IF NOT EXISTS idx_spans_trace ON telemetry_spans(trace_id, start_time_ms ASC);

CREATE TABLE IF NOT EXISTS token_usage_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL DEFAULT '',
    trace_id TEXT NOT NULL DEFAULT '',
    span_id TEXT NOT NULL DEFAULT '',
    service_name TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    model_name TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    candidates_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL NOT NULL DEFAULT 0.0,
    latency_ms REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tokens_session ON token_usage_ledger(session_id, id ASC);

CREATE TABLE IF NOT EXISTS skill_activation_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL DEFAULT '',
    trace_id TEXT NOT NULL DEFAULT '',
    service_name TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    skill_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_skills_session ON skill_activation_ledger(session_id, id ASC);
"""
