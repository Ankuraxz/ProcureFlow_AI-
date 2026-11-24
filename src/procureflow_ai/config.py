"""Central configuration, model routing, port bindings, and FinOps pricing tables."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)

SKILLS_DIR = Path(
    os.getenv("PROCUREFLOW_SKILLS_DIR")
    or os.getenv("SKILLS_DIR")
    or str(PROJECT_ROOT / "skills")
)

APP_NAME = "procureflow_ai"

# Model tier defaults (overridable via environment variables)
ORCHESTRATOR_MODEL = os.getenv("ORCHESTRATOR_MODEL", "gemini-3.5-flash")
SETTLEMENT_MODEL = os.getenv("SETTLEMENT_MODEL", "gemini-3.5-flash")
SLM_MODEL = os.getenv("SLM_MODEL", "gemini-3.5-flash-lite")
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "gemini-3.5-flash")

# Service ports & base URLs
GATEWAY_HOST = os.getenv("GATEWAY_HOST", "127.0.0.1")
GATEWAY_PORT = int(os.getenv("GATEWAY_PORT", "8000"))
CATALOG_PORT = int(os.getenv("CATALOG_PORT", "8001"))
POLICY_PORT = int(os.getenv("POLICY_PORT", "8002"))
SETTLEMENT_PORT = int(os.getenv("SETTLEMENT_PORT", "8003"))
UI_PORT = int(os.getenv("UI_PORT", "5173"))

# Policy & AP2 Governance Thresholds
AP2_AUTO_LIMIT_USD = float(os.getenv("AP2_AUTO_LIMIT_USD", "500.0"))
BUDGET_WARNING_RATIO = float(os.getenv("BUDGET_WARNING_RATIO", "0.80"))

# Per-1M token USD pricing estimates for real-time FinOps ledger
MODEL_PRICING_PER_1M_USD: dict[str, dict[str, float]] = {
    "gemini-3.5-flash": {
        "input": 0.30,
        "output": 2.50,
        "cached": 0.075,
    },
    "gemini-3.5-flash-lite": {
        "input": 0.10,
        "output": 0.40,
        "cached": 0.025,
    },
    "gemini-2.5-flash": {
        "input": 0.30,
        "output": 2.50,
        "cached": 0.075,
    },
    "gemini-2.5-flash-lite": {
        "input": 0.10,
        "output": 0.40,
        "cached": 0.025,
    },
}


def _resolve_data_dir() -> Path:
    """Resolve base data directory from `PROCUREFLOW_DATA_DIR` / `DATA_DIR` or repo `data/`."""
    raw_dir = os.getenv("PROCUREFLOW_DATA_DIR") or os.getenv("DATA_DIR")
    return Path(raw_dir).expanduser().resolve() if raw_dir else (PROJECT_ROOT / "data")


def get_domain_db_path() -> Path:
    """Resolve the relational/domain SQLite database path dynamically from env."""
    raw = (
        os.getenv("PROCUREFLOW_DOMAIN_DB_PATH")
        or os.getenv("DOMAIN_DB_PATH")
        or str(_resolve_data_dir() / "procureflow_domain.db")
    )
    path = Path(raw).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def get_session_db_path() -> Path:
    """Resolve the ADK DatabaseSessionService SQLite database path dynamically from env."""
    raw = (
        os.getenv("PROCUREFLOW_SESSIONS_DB_PATH")
        or os.getenv("SESSION_DB_PATH")
        or os.getenv("SESSIONS_DB_PATH")
        or str(_resolve_data_dir() / "procureflow_sessions.db")
    )
    path = Path(raw).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def get_session_db_url(db_path: Path | str | None = None) -> str:
    """Return an async SQLAlchemy URL (`sqlite+aiosqlite:///...`) for ADK sessions."""
    resolved = Path(db_path).expanduser().resolve() if db_path else get_session_db_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+aiosqlite:///{resolved.as_posix()}"


def get_skills_dir() -> Path:
    """Return the procedural memory skills directory."""
    raw = os.getenv("PROCUREFLOW_SKILLS_DIR") or os.getenv("SKILLS_DIR")
    return Path(raw).expanduser().resolve() if raw else SKILLS_DIR


@dataclass(frozen=True)
class Settings:
    """Runtime settings snapshot supporting clean environment overrides in tests and Cloud Run."""

    app_name: str
    orchestrator_model: str
    settlement_model: str
    slm_model: str
    judge_model: str
    gateway_host: str
    gateway_port: int
    catalog_port: int
    policy_port: int
    settlement_port: int
    catalog_agent_url: str
    policy_agent_url: str
    settlement_agent_url: str
    domain_db_path: Path
    session_db_path: Path
    skills_dir: Path
    ap2_auto_limit_usd: float
    budget_warning_ratio: float
    enable_cloud_trace: bool
    cors_origins: tuple[str, ...]


def get_settings() -> Settings:
    """Build current settings from environment variables."""
    catalog_port = int(os.getenv("CATALOG_PORT", str(CATALOG_PORT)))
    policy_port = int(os.getenv("POLICY_PORT", str(POLICY_PORT)))
    settlement_port = int(os.getenv("SETTLEMENT_PORT", str(SETTLEMENT_PORT)))
    gateway_port = int(os.getenv("GATEWAY_PORT", str(GATEWAY_PORT)))
    cors_raw = os.getenv(
        "CORS_ORIGINS",
        f"http://localhost:{UI_PORT},http://127.0.0.1:{UI_PORT},http://localhost:3000",
    )
    cors_origins = tuple(o.strip() for o in cors_raw.split(",") if o.strip())

    catalog_url = (
        os.getenv("CATALOG_AGENT_URL")
        or os.getenv("CATALOG_A2A_URL")
        or f"http://localhost:{catalog_port}"
    ).rstrip("/")
    policy_url = (
        os.getenv("POLICY_AGENT_URL")
        or os.getenv("POLICY_A2A_URL")
        or f"http://localhost:{policy_port}"
    ).rstrip("/")
    settlement_url = (
        os.getenv("SETTLEMENT_AGENT_URL")
        or os.getenv("SETTLEMENT_A2A_URL")
        or f"http://localhost:{settlement_port}"
    ).rstrip("/")

    return Settings(
        app_name=APP_NAME,
        orchestrator_model=os.getenv("ORCHESTRATOR_MODEL", ORCHESTRATOR_MODEL),
        settlement_model=os.getenv("SETTLEMENT_MODEL", SETTLEMENT_MODEL),
        slm_model=os.getenv("SLM_MODEL", SLM_MODEL),
        judge_model=os.getenv("JUDGE_MODEL", JUDGE_MODEL),
        gateway_host=os.getenv("GATEWAY_HOST", GATEWAY_HOST),
        gateway_port=gateway_port,
        catalog_port=catalog_port,
        policy_port=policy_port,
        settlement_port=settlement_port,
        catalog_agent_url=catalog_url,
        policy_agent_url=policy_url,
        settlement_agent_url=settlement_url,
        domain_db_path=get_domain_db_path(),
        session_db_path=get_session_db_path(),
        skills_dir=get_skills_dir(),
        ap2_auto_limit_usd=float(os.getenv("AP2_AUTO_LIMIT_USD", str(AP2_AUTO_LIMIT_USD))),
        budget_warning_ratio=float(
            os.getenv("BUDGET_WARNING_RATIO", str(BUDGET_WARNING_RATIO))
        ),
        enable_cloud_trace=os.getenv("ENABLE_CLOUD_TRACE", "false").lower()
        in ("1", "true", "yes"),
        cors_origins=cors_origins,
    )


def estimate_token_cost_usd(
    model_name: str,
    prompt_tokens: int,
    candidates_tokens: int,
    cached_tokens: int = 0,
) -> float:
    """Compute estimated USD cost for a single LLM invocation."""
    normalized = model_name.split("/")[-1]
    rates = MODEL_PRICING_PER_1M_USD.get(normalized)
    if rates is None:
        rates = (
            MODEL_PRICING_PER_1M_USD["gemini-3.5-flash-lite"]
            if "lite" in normalized
            else MODEL_PRICING_PER_1M_USD["gemini-3.5-flash"]
        )
    billable_prompt = max(0, prompt_tokens - cached_tokens)
    cost = (
        (billable_prompt / 1_000_000.0) * rates["input"]
        + (max(0, cached_tokens) / 1_000_000.0) * rates["cached"]
        + (max(0, candidates_tokens) / 1_000_000.0) * rates["output"]
    )
    return round(cost, 8)
