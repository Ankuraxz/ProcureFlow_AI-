"""CLI supervisor and microservice entrypoint (`procureflow-ai start | serve-service | seed`)."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Sequence
import urllib.error
import urllib.request

import uvicorn

from .api.a2a_servers import (
    create_catalog_a2a_app,
    create_policy_a2a_app,
    create_settlement_a2a_app,
)
from .api.gateway import create_gateway_app
from .config import PROJECT_ROOT, UI_PORT, get_settings
from .db.seed import init_and_seed_db


def _detect_nvm_node_bin() -> Path | None:
    nvm_versions = Path.home() / ".nvm" / "versions" / "node"
    if not nvm_versions.is_dir():
        return None
    bins = sorted(p for p in nvm_versions.glob("*/bin") if (p / "npm").exists())
    return bins[-1] if bins else None


def _default_port_for_role(role: str) -> int:
    env_port = os.getenv("PORT")
    if env_port:
        return int(env_port)
    settings = get_settings()
    return {
        "gateway": settings.gateway_port,
        "catalog": settings.catalog_port,
        "policy": settings.policy_port,
        "settlement": settings.settlement_port,
    }[role]


def _wait_for_http_ready(url: str, timeout_sec: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as resp:
                if 200 <= resp.status < 300:
                    return True
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            time.sleep(0.35)
    return False


def cmd_seed(args: argparse.Namespace) -> int:
    db_path = init_and_seed_db(reset=bool(args.reset))
    mode = "Reset & re-seeded" if args.reset else "Initialized & seeded"
    print(f"[procureflow-ai] {mode} SQLite domain store at: {db_path}")
    return 0


def cmd_serve_service(args: argparse.Namespace) -> int:
    role: str = args.role
    bind_host: str = args.host
    port: int = int(args.port) if args.port else _default_port_for_role(role)
    public_host = os.getenv(
        "PUBLIC_HOST",
        "localhost" if bind_host in ("127.0.0.1", "0.0.0.0", "localhost") else bind_host,
    )
    public_protocol = os.getenv("PUBLIC_PROTOCOL", "http")

    init_and_seed_db()

    if role == "gateway":
        asgi_app = create_gateway_app()
    elif role == "catalog":
        asgi_app = create_catalog_a2a_app(
            host=public_host,
            port=port,
            protocol=public_protocol,
        )
    elif role == "policy":
        asgi_app = create_policy_a2a_app(
            host=public_host,
            port=port,
            protocol=public_protocol,
        )
    elif role == "settlement":
        asgi_app = create_settlement_a2a_app(
            host=public_host,
            port=port,
            protocol=public_protocol,
        )
    else:
        raise ValueError(f"Unsupported service role: {role}")

    print(f"[procureflow-ai] Serving role='{role}' on http://{bind_host}:{port}")
    uvicorn.run(
        asgi_app,
        host=bind_host,
        port=port,
        log_level=args.log_level,
    )
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    settings = get_settings()
    bind_host: str = args.host
    init_and_seed_db()

    services = [
        ("catalog", settings.catalog_port, f"http://127.0.0.1:{settings.catalog_port}/.well-known/agent.json"),
        ("policy", settings.policy_port, f"http://127.0.0.1:{settings.policy_port}/.well-known/agent.json"),
        ("settlement", settings.settlement_port, f"http://127.0.0.1:{settings.settlement_port}/.well-known/agent.json"),
        ("gateway", settings.gateway_port, f"http://127.0.0.1:{settings.gateway_port}/api/health"),
    ]

    procs: list[tuple[str, subprocess.Popen[bytes]]] = []

    def _shutdown_all() -> None:
        for name, proc in reversed(procs):
            if proc.poll() is None:
                print(f"[procureflow-ai] Stopping {name} (pid={proc.pid})...")
                proc.terminate()
        deadline = time.monotonic() + 5.0
        for _, proc in reversed(procs):
            while proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
            if proc.poll() is None:
                proc.kill()

    stop_requested = False

    def _handle_signal(signum: int, _frame: object) -> None:
        nonlocal stop_requested
        print(f"\n[procureflow-ai] Received signal {signum}, shutting down fleet...")
        stop_requested = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        for role, port, probe_url in services:
            cmd = [
                sys.executable,
                "-m",
                "procureflow_ai.cli",
                "serve-service",
                "--role",
                role,
                "--host",
                bind_host,
                "--port",
                str(port),
                "--log-level",
                args.log_level,
            ]
            proc = subprocess.Popen(cmd, cwd=str(PROJECT_ROOT))
            procs.append((role, proc))
            if not _wait_for_http_ready(probe_url, timeout_sec=35.0):
                print(
                    f"[procureflow-ai] ERROR: Service '{role}' failed readiness check at {probe_url}",
                    file=sys.stderr,
                )
                _shutdown_all()
                return 1
            print(f"[procureflow-ai] Ready: {role} -> {probe_url}")

        if args.with_ui:
            frontend_dir = Path(PROJECT_ROOT) / "frontend"
            if (frontend_dir / "package.json").exists():
                env = os.environ.copy()
                nvm_bin = _detect_nvm_node_bin()
                if nvm_bin is not None:
                    env["PATH"] = f"{nvm_bin}:{env.get('PATH', '')}"
                ui_cmd = ["npm", "run", "dev", "--", "--port", str(UI_PORT)]
                ui_proc = subprocess.Popen(ui_cmd, cwd=str(frontend_dir), env=env)
                procs.append(("vite-ui", ui_proc))
                print(f"[procureflow-ai] Started React UI at http://localhost:{UI_PORT}")
            else:
                print(
                    f"[procureflow-ai] Note: {frontend_dir / 'package.json'} not found; skipping --with-ui."
                )

        print(
            f"\n[procureflow-ai] Multi-agent A2A mesh online! Gateway: http://localhost:{settings.gateway_port}"
        )
        while not stop_requested:
            for name, proc in procs:
                rc = proc.poll()
                if rc is not None:
                    print(
                        f"[procureflow-ai] Subprocess '{name}' exited unexpectedly with code {rc}.",
                        file=sys.stderr,
                    )
                    stop_requested = True
                    break
            time.sleep(0.5)
    finally:
        _shutdown_all()

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="procureflow-ai",
        description="ProcureFlow AI — Multi-Agent ADK/A2A/A2UI/AP2 Enterprise Procurement Desk",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start", help="Launch all 3 A2A sub-servers + FastAPI Gateway (+ optional UI)")
    p_start.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    p_start.add_argument("--with-ui", action="store_true", help="Also start Vite React frontend on port 5173")
    p_start.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    p_start.set_defaults(func=cmd_start)

    p_serve = sub.add_parser("serve-service", help="Run a single microservice role (local or Cloud Run)")
    p_serve.add_argument(
        "--role",
        required=True,
        choices=["gateway", "catalog", "policy", "settlement"],
        help="Microservice role to run",
    )
    p_serve.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    p_serve.add_argument("--port", type=int, default=0, help="Bind port (defaults to role port or $PORT)")
    p_serve.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    p_serve.set_defaults(func=cmd_serve_service)

    p_seed = sub.add_parser("seed", help="Initialize or reset the SQLite domain database")
    p_seed.add_argument("--reset", action="store_true", help="Wipe dynamic orders/mandates and restore demo budgets")
    p_seed.set_defaults(func=cmd_seed)

    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    rc = int(args.func(args))
    if rc != 0:
        sys.exit(rc)


if __name__ == "__main__":
    main()
