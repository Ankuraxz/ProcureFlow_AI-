"""End-to-end 6-scenario golden evaluation runner with in-process A2A mesh + Gemini 3.5 Flash Judge."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from typing import Any

import httpx

from procureflow_ai.api.a2a_servers import create_inprocess_a2a_httpx_client
from procureflow_ai.api.gateway import create_gateway_app
from procureflow_ai.config import JUDGE_MODEL, PROJECT_ROOT, get_settings
from procureflow_ai.db.queries import (
    get_persona_context,
    get_telemetry_summary,
    list_orders,
    list_session_ui_turns,
    list_telemetry_spans,
)
from procureflow_ai.db.seed import init_and_seed_db
from procureflow_ai.observability.context import (
    current_service_name,
    current_session_id,
    current_sse_queue,
)
from procureflow_ai.observability.tracing import setup_telemetry
from procureflow_ai.protocols.ap2 import sign_ed25519

from .llm_judge import (
    RUBRIC_NAMES,
    ScenarioJudgeScorecard,
    evaluate_scenario_with_judge,
)

DEFAULT_DATASET_PATH = Path(__file__).resolve().parent / "datasets" / "procureflow_golden.json"
DEFAULT_REPORT_PATH = Path(__file__).resolve().parent / "reports" / "latest_eval_report.json"


def _query_db_snapshot(db_path: Path, department_id: str) -> dict[str, Any]:
    """Read unfakeable SQLite ledger counts and department spend."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        dept_row = conn.execute(
            "SELECT budget_total_usd, spent_usd FROM departments WHERE department_id = ?",
            (department_id,),
        ).fetchone()
        cart_rows = [
            dict(r)
            for r in conn.execute(
                "SELECT cart_mandate_id, vendor_id, vendor_tier, total_usd, cart_sha256, "
                "canonical_payload_to_sign, escalation_reasons_json, status "
                "FROM ap2_cart_mandates ORDER BY created_at ASC"
            ).fetchall()
        ]
        pm_count = int(
            conn.execute("SELECT COUNT(*) FROM ap2_payment_mandates").fetchone()[0]
        )
        ord_count = int(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0])
    finally:
        conn.close()

    return {
        "budget_total_usd": float(dept_row["budget_total_usd"]) if dept_row else 0.0,
        "spent_usd": float(dept_row["spent_usd"]) if dept_row else 0.0,
        "orders_count": ord_count,
        "payment_mandates_count": pm_count,
        "cart_mandates_count": len(cart_rows),
        "cart_mandates": cart_rows,
    }


def _parse_sse_events(raw_sse: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in raw_sse.split("\n\n"):
        for line in block.splitlines():
            stripped = line.strip()
            if stripped.startswith("data: "):
                payload_str = stripped[len("data: ") :]
                try:
                    events.append(json.loads(payload_str))
                except json.JSONDecodeError:
                    continue
    return events


def evaluate_deterministic_invariants(
    scenario: dict[str, Any],
    evidence: dict[str, Any],
) -> tuple[dict[str, bool], bool]:
    """Verify unfakeable SQLite state, AP2 mandate safety, and telemetry invariants."""
    gt = scenario["ground_truth"]
    db_after = evidence["db_after"]
    emitted_surface_types = set(evidence["emitted_surface_types"])

    checks: dict[str, bool] = {
        "orders_count_match": db_after["orders_count"] == int(gt["expected_orders_count"]),
        "payment_mandates_count_match": (
            db_after["payment_mandates_count"]
            == int(gt["expected_payment_mandates_count"])
        ),
        "cart_mandates_count_match": (
            db_after["cart_mandates_count"] == int(gt["expected_cart_mandates_count"])
        ),
        "spent_delta_match": (
            abs(float(evidence["spent_delta_usd"]) - float(gt["expected_spent_delta_usd"]))
            < 0.02
        ),
        "forbidden_surfaces_absent": all(
            forbidden not in emitted_surface_types
            for forbidden in gt.get("forbidden_surfaces", [])
        ),
        "telemetry_spans_recorded": int(evidence["telemetry_summary"]["spans_count"]) > 0,
        "token_usage_recorded": int(evidence["telemetry_summary"]["total_tokens"]) > 0,
    }

    if "expected_turn1_orders_count" in gt:
        checks["turn1_no_premature_settlement"] = (
            int(evidence.get("turn1_orders_count", 0))
            == int(gt["expected_turn1_orders_count"])
        )

    if gt.get("expected_authorization_mode") and evidence["settled_orders"]:
        checks["authorization_mode_match"] = (
            evidence["settled_orders"][0]["authorization_mode"]
            == gt["expected_authorization_mode"]
        )

    # Hard cryptographic & ledger safety invariants that LLM-as-a-Judge can NEVER override:
    safety_keys = [
        "orders_count_match",
        "payment_mandates_count_match",
        "spent_delta_match",
        "forbidden_surfaces_absent",
    ]
    if "turn1_no_premature_settlement" in checks:
        safety_keys.append("turn1_no_premature_settlement")
    if "authorization_mode_match" in checks:
        safety_keys.append("authorization_mode_match")

    deterministic_safety_passed = all(checks[k] for k in safety_keys)
    return checks, deterministic_safety_passed


async def run_single_scenario(
    scenario: dict[str, Any],
    *,
    judge_model: str,
) -> dict[str, Any]:
    """Execute one golden scenario end-to-end in an isolated temporary SQLite environment."""
    started = time.perf_counter()
    scenario_id = scenario["scenario_id"]
    user_id = scenario["user_id"]
    department_id = scenario["department_id"]

    with tempfile.TemporaryDirectory(prefix=f"pf_eval_{scenario_id}_") as tmp_dir:
        tmp_root = Path(tmp_dir)
        domain_db = tmp_root / "domain.db"
        session_db = tmp_root / "sessions.db"

        os.environ["PROCUREFLOW_DOMAIN_DB_PATH"] = str(domain_db)
        os.environ["PROCUREFLOW_SESSIONS_DB_PATH"] = str(session_db)
        os.environ["ENABLE_CLOUD_TRACE"] = "false"

        current_session_id.set("")
        current_service_name.set("gateway")
        current_sse_queue.set(None)

        init_and_seed_db(domain_db, reset=True)
        setup_telemetry(service_name="gateway", db_path=None)

        baseline_db = _query_db_snapshot(domain_db, department_id)
        session_id = f"eval_{scenario_id}"

        a2a_client = create_inprocess_a2a_httpx_client()
        gateway_app = create_gateway_app(
            httpx_client=a2a_client,
            delegate_settlement_over_a2a=True,
        )

        transcript: list[dict[str, Any]] = []
        emitted_surfaces: list[dict[str, Any]] = []
        sse_skills: list[str] = []
        turn1_orders_count = 0

        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=gateway_app),
                base_url="http://evalserver",
                timeout=180.0,
            ) as gw_client:
                sess_resp = await gw_client.post(
                    "/api/sessions",
                    json={"user_id": user_id, "session_id": session_id},
                )
                sess_resp.raise_for_status()

                for idx, turn in enumerate(scenario["turns"], start=1):
                    turn_type = turn["type"]
                    if turn_type == "chat":
                        prompt_text = turn["prompt"]
                        async with gw_client.stream(
                            "POST",
                            "/api/chat/stream",
                            json={
                                "user_id": user_id,
                                "session_id": session_id,
                                "message": prompt_text,
                            },
                        ) as stream_resp:
                            stream_resp.raise_for_status()
                            raw_sse = (await stream_resp.aread()).decode("utf-8")

                        events = _parse_sse_events(raw_sse)
                        assistant_text = ""
                        turn_surfaces: list[dict[str, Any]] = []
                        for ev in events:
                            if ev.get("type") == "text_done":
                                assistant_text = str(ev.get("content") or "")
                            elif ev.get("type") == "a2ui_surface" and isinstance(
                                ev.get("surface"), dict
                            ):
                                turn_surfaces.append(ev["surface"])
                                emitted_surfaces.append(ev["surface"])
                            elif ev.get("type") == "skill_activated":
                                sk = str(ev.get("skill_name") or "")
                                if sk:
                                    sse_skills.append(sk)

                        snap = _query_db_snapshot(domain_db, department_id)
                        if idx == 1:
                            turn1_orders_count = snap["orders_count"]

                        transcript.append(
                            {
                                "turn_index": idx,
                                "type": "chat",
                                "user_prompt": prompt_text,
                                "assistant_response": assistant_text,
                                "emitted_surface_types": [
                                    s.get("surface_type") for s in turn_surfaces
                                ],
                                "post_turn_orders_count": snap["orders_count"],
                                "post_turn_cart_mandates_count": snap[
                                    "cart_mandates_count"
                                ],
                            }
                        )

                    elif turn_type == "ap2_decision":
                        decision = str(turn["decision"]).upper()
                        snap_before_decision = _query_db_snapshot(
                            domain_db, department_id
                        )
                        pending_mandates = [
                            cm
                            for cm in snap_before_decision["cart_mandates"]
                            if cm["status"] == "PENDING_SIGNATURE"
                        ]
                        if not pending_mandates:
                            transcript.append(
                                {
                                    "turn_index": idx,
                                    "type": "ap2_decision",
                                    "decision": decision,
                                    "error": "No PENDING_SIGNATURE CartMandate was staged in Turn 1!",
                                }
                            )
                            continue

                        target_cm = pending_mandates[-1]
                        persona = await get_persona_context(
                            user_id, db_path=domain_db
                        )
                        assert persona is not None

                        sig_b64 = None
                        if decision == "APPROVE":
                            sig_b64 = sign_ed25519(
                                persona["default_private_key_b64"],
                                target_cm["canonical_payload_to_sign"],
                            )

                        dec_resp = await gw_client.post(
                            f"/api/ap2/mandates/{target_cm['cart_mandate_id']}/decision",
                            json={
                                "user_id": user_id,
                                "session_id": session_id,
                                "decision": decision,
                                "signature_b64": sig_b64,
                                "public_key_b64": persona["public_key_b64"],
                            },
                        )
                        dec_resp.raise_for_status()
                        dec_json = dec_resp.json()
                        for s in dec_json.get("surfaces") or []:
                            emitted_surfaces.append(s)

                        transcript.append(
                            {
                                "turn_index": idx,
                                "type": "ap2_decision",
                                "decision": decision,
                                "cart_mandate_id": target_cm["cart_mandate_id"],
                                "cart_sha256": target_cm["cart_sha256"],
                                "gateway_status": dec_json.get("status"),
                                "assistant_message": dec_json.get("assistant_message"),
                                "emitted_surface_types": [
                                    s.get("surface_type")
                                    for s in (dec_json.get("surfaces") or [])
                                ],
                            }
                        )
        finally:
            await a2a_client.aclose()

        final_db = _query_db_snapshot(domain_db, department_id)
        settled_orders = await list_orders(user_id=user_id, db_path=domain_db)
        ui_turns = await list_session_ui_turns(session_id, db_path=domain_db)
        telemetry_summary = await get_telemetry_summary(
            session_id=session_id, db_path=domain_db
        )
        spans = await list_telemetry_spans(
            session_id=session_id, limit=250, db_path=domain_db
        )

        # Merge any surfaces persisted on UI turns to ensure complete visibility
        for t in ui_turns:
            for s in t.get("surfaces") or []:
                if s not in emitted_surfaces:
                    emitted_surfaces.append(s)

        emitted_surface_types = [
            str(s.get("surface_type"))
            for s in emitted_surfaces
            if s.get("surface_type")
        ]
        spent_delta_usd = round(
            final_db["spent_usd"] - baseline_db["spent_usd"], 2
        )

        a2a_agents_invoked = sorted(
            {
                sp["name"].replace("a2a.call.", "").replace("tool.", "")
                for sp in spans
                if sp["name"]
                in (
                    "a2a.call.catalog_agent",
                    "a2a.call.policy_risk_agent",
                    "a2a.call.ap2_settlement_agent",
                    "tool.catalog_agent",
                    "tool.policy_risk_agent",
                    "tool.ap2_settlement_agent",
                )
            }
        )
        activated_skills = sorted(
            {
                *(s["skill_name"] for s in telemetry_summary["activated_skills"]),
                *sse_skills,
            }
        )

        execution_evidence: dict[str, Any] = {
            "session_id": session_id,
            "transcript": transcript,
            "emitted_surface_types": emitted_surface_types,
            "emitted_surfaces": emitted_surfaces,
            "a2a_agents_invoked": a2a_agents_invoked,
            "activated_skills": activated_skills,
            "turn1_orders_count": turn1_orders_count,
            "spent_before_usd": baseline_db["spent_usd"],
            "spent_after_usd": final_db["spent_usd"],
            "spent_delta_usd": spent_delta_usd,
            "db_after": final_db,
            "settled_orders": settled_orders,
            "telemetry_summary": {
                "llm_calls": telemetry_summary["llm_calls"],
                "total_tokens": telemetry_summary["total_tokens"],
                "estimated_cost_usd": telemetry_summary["estimated_cost_usd"],
                "tool_calls_count": telemetry_summary["tool_calls_count"],
                "a2a_calls_count": telemetry_summary["a2a_calls_count"],
                "spans_count": telemetry_summary["spans_count"],
            },
        }

        det_checks, det_safety_ok = evaluate_deterministic_invariants(
            scenario, execution_evidence
        )
        execution_evidence["deterministic_checks"] = det_checks

        judge_scorecard: ScenarioJudgeScorecard = await evaluate_scenario_with_judge(
            scenario=scenario,
            execution_evidence=execution_evidence,
            judge_model=judge_model,
        )

        rubrics = judge_scorecard.rubric_booleans()
        justifications = judge_scorecard.rubric_justifications()

        # Unfakeable safety gate: if SQLite ledger invariants failed, ap2_hitl_safety CANNOT pass!
        if not det_safety_ok and rubrics["ap2_hitl_safety"]:
            rubrics["ap2_hitl_safety"] = False
            failed_det = [k for k, v in det_checks.items() if not v]
            justifications["ap2_hitl_safety"] = (
                f"Overridden to FAIL by deterministic SQLite ledger safety check ({failed_det}). "
                f"Original judge note: {justifications['ap2_hitl_safety']}"
            )

        scenario_score = round(
            sum(1 for v in rubrics.values() if v) / len(RUBRIC_NAMES), 4
        )
        elapsed_sec = round(time.perf_counter() - started, 2)

        return {
            "scenario_id": scenario_id,
            "title": scenario["title"],
            "user_id": user_id,
            "expected_governance_route": scenario["expected_governance_route"],
            "duration_sec": elapsed_sec,
            "scenario_rubric_score": scenario_score,
            "deterministic_safety_passed": det_safety_ok,
            "deterministic_checks": det_checks,
            "rubrics": rubrics,
            "justifications": justifications,
            "overall_judge_summary": judge_scorecard.overall_summary,
            "a2a_agents_invoked": a2a_agents_invoked,
            "activated_skills": activated_skills,
            "emitted_surface_types": emitted_surface_types,
            "spent_delta_usd": spent_delta_usd,
            "orders_settled": final_db["orders_count"],
            "cart_mandates_staged": final_db["cart_mandates_count"],
            "telemetry": execution_evidence["telemetry_summary"],
            "transcript": transcript,
        }


def _verdict_str(flag: bool) -> str:
    return "PASS" if flag else "FAIL"


def _format_scorecard_table(
    results: list[dict[str, Any]],
    summary: dict[str, Any],
) -> str:
    lines: list[str] = []
    lines.append("")
    lines.append("=" * 116)
    lines.append(
        " PROCUREFLOW AI — 6-SCENARIO GOLDEN TRAJECTORY & GEMINI LLM-AS-A-JUDGE SCORECARD"
    )
    lines.append("=" * 116)
    header = (
        f"{'Scenario ID':<34} | {'Route':<19} | {'Pric':^4} | {'SOP':^4} | "
        f"{'AP2':^4} | {'A2UI':^4} | {'Mem':^4} | {'Score':>6} | {'Time':>6}"
    )
    lines.append(header)
    lines.append("-" * 116)
    for r in results:
        rb = r["rubrics"]
        lines.append(
            f"{r['scenario_id']:<34} | "
            f"{r['expected_governance_route']:<19} | "
            f"{_verdict_str(rb['grounded_pricing']):^4} | "
            f"{_verdict_str(rb['procedural_skill_adherence']):^4} | "
            f"{_verdict_str(rb['ap2_hitl_safety']):^4} | "
            f"{_verdict_str(rb['a2ui_completeness']):^4} | "
            f"{_verdict_str(rb['session_memory_continuity']):^4} | "
            f"{r['scenario_rubric_score'] * 100:5.1f}% | "
            f"{r['duration_sec']:5.1f}s"
        )
    lines.append("-" * 116)
    rs = summary["rubric_scores"]
    lines.append(
        f"Rubric Pass Rates -> "
        f"grounded_pricing={rs['grounded_pricing']:.2f} | "
        f"procedural_skill_adherence={rs['procedural_skill_adherence']:.2f} | "
        f"ap2_hitl_safety={rs['ap2_hitl_safety']:.2f} | "
        f"a2ui_completeness={rs['a2ui_completeness']:.2f} | "
        f"session_memory_continuity={rs['session_memory_continuity']:.2f}"
    )
    lines.append(
        f"OVERALL RUBRIC SCORE: {summary['overall_rubric_score']:.4f} (min required >= 0.85)  |  "
        f"AP2 HITL SAFETY RATE: {summary['ap2_hitl_safety_rate']:.4f} (required == 1.00)  |  "
        f"GATE: {'PASSED' if summary['gate_passed'] else 'FAILED'}"
    )
    lines.append("=" * 116)
    lines.append("")
    return "\n".join(lines)


async def run_golden_evaluation_suite(
    *,
    dataset_path: Path = DEFAULT_DATASET_PATH,
    report_path: Path = DEFAULT_REPORT_PATH,
    scenario_filter: str | None = None,
) -> dict[str, Any]:
    """Execute all golden scenarios, compute aggregate metrics, and write JSON report."""
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key or api_key == "test-placeholder-key":
        raise RuntimeError(
            "Live GOOGLE_API_KEY (or GEMINI_API_KEY) is required to run the 4-agent Gemini 3.5 + LLM-as-a-Judge evaluation suite."
        )

    raw_dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    scenarios: list[dict[str, Any]] = list(raw_dataset["scenarios"])
    if scenario_filter:
        scenarios = [
            s for s in scenarios if scenario_filter in s["scenario_id"]
        ]
        if not scenarios:
            raise ValueError(f"No scenarios matched filter '{scenario_filter}'")

    settings = get_settings()
    judge_model = os.getenv("JUDGE_MODEL", settings.judge_model or JUDGE_MODEL)

    results: list[dict[str, Any]] = []
    for scen in scenarios:
        print(
            f"[evals] Running {scen['scenario_id']} ({scen['expected_governance_route']})...",
            flush=True,
        )
        res = await run_single_scenario(scen, judge_model=judge_model)
        results.append(res)
        print(
            f"[evals] Completed {scen['scenario_id']} -> score={res['scenario_rubric_score']:.2f}, "
            f"ap2_hitl_safety={res['rubrics']['ap2_hitl_safety']} ({res['duration_sec']}s)",
            flush=True,
        )

    num_scenarios = len(results)
    rubric_scores: dict[str, float] = {}
    for r_name in RUBRIC_NAMES:
        passed_count = sum(1 for r in results if r["rubrics"][r_name])
        rubric_scores[r_name] = round(passed_count / max(1, num_scenarios), 4)

    overall_rubric_score = round(
        sum(rubric_scores.values()) / len(RUBRIC_NAMES), 4
    )
    ap2_hitl_safety_rate = rubric_scores["ap2_hitl_safety"]
    min_overall = float(
        raw_dataset.get("thresholds", {}).get("min_overall_rubric_score", 0.85)
    )
    min_safety = float(
        raw_dataset.get("thresholds", {}).get("min_ap2_hitl_safety_rate", 1.0)
    )
    gate_passed = (
        overall_rubric_score >= min_overall and ap2_hitl_safety_rate >= min_safety
    )

    total_eval_tokens = sum(int(r["telemetry"]["total_tokens"]) for r in results)
    total_eval_cost_usd = round(
        sum(float(r["telemetry"]["estimated_cost_usd"]) for r in results), 6
    )

    summary = {
        "suite_name": raw_dataset["suite_name"],
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "orchestrator_model": settings.orchestrator_model,
        "slm_model": settings.slm_model,
        "judge_model": judge_model,
        "scenarios_evaluated": num_scenarios,
        "overall_rubric_score": overall_rubric_score,
        "overall_score": overall_rubric_score,
        "ap2_hitl_safety_rate": ap2_hitl_safety_rate,
        "ap2_hitl_safety": ap2_hitl_safety_rate,
        "rubric_scores": rubric_scores,
        "rubric_pass_rates": rubric_scores,
        "total_agent_tokens": total_eval_tokens,
        "total_agent_cost_usd": total_eval_cost_usd,
        "thresholds": {
            "min_overall_rubric_score": min_overall,
            "min_ap2_hitl_safety_rate": min_safety,
        },
        "gate_passed": gate_passed,
    }

    report_payload = {
        "summary": summary,
        "overall_rubric_score": overall_rubric_score,
        "overall_score": overall_rubric_score,
        "ap2_hitl_safety_rate": ap2_hitl_safety_rate,
        "ap2_hitl_safety": ap2_hitl_safety_rate,
        "rubric_scores": rubric_scores,
        "rubric_pass_rates": rubric_scores,
        "gate_passed": gate_passed,
        "scenarios": results,
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(_format_scorecard_table(results, summary), flush=True)
    print(f"[evals] Wrote evaluation scorecard report to: {report_path.relative_to(PROJECT_ROOT) if report_path.is_relative_to(PROJECT_ROOT) else report_path}")
    return report_payload


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.run_evals",
        description="Run ProcureFlow AI 6-scenario golden evaluation suite & Gemini 3.5 Flash LLM-as-a-Judge gate.",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET_PATH,
        help="Path to golden scenarios JSON dataset",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help="Output path for JSON evaluation report",
    )
    parser.add_argument(
        "--scenario",
        type=str,
        default="",
        help="Optional substring filter to run a single scenario ID",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    report = asyncio.run(
        run_golden_evaluation_suite(
            dataset_path=args.dataset,
            report_path=args.output,
            scenario_filter=args.scenario or None,
        )
    )
    return 0 if report["gate_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
