"""Evaluation Runner (Level 1, 2, 3)."""

import hashlib
import time
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from backend.app.db.session import get_db_context
from backend.app.db.models import EvalRun, EvalResult, Case, Dataset, Target
from backend.evalkit.assertions.registry import run_assertion
from backend.evalkit.targets.adapters import (
    call_http_target, call_cli_target, call_python_target, call_replay_target
)
import backend.evalkit.assertions.builtin.standard  # ensure assertions loaded


def generate_fake_output(case_input: Any, scenario: str) -> Dict[str, Any]:
    """Deterministic fake target output for CI testing without network."""
    if scenario == "answer_in_docs":
        return {
            "text": "Based on documentation [1], the method provides high accuracy.",
            "citations": [1],
            "latency_ms": 120,
            "cost_usd": 0.001
        }
    elif scenario == "answer_not_in_docs":
        return {
            "text": "The requested topic cannot be found in the provided documentation.",
            "citations": [],
            "latency_ms": 95,
            "cost_usd": 0.0008
        }
    elif scenario == "regressed_prompt":
        # Missing citations and hallucinated answer to trigger gate failures
        return {
            "text": "I think the answer is 42.",
            "citations": [],
            "latency_ms": 110,
            "cost_usd": 0.001
        }
    return {
        "text": f"Simulated output for scenario {scenario} [1]",
        "citations": [1],
        "latency_ms": 100,
        "cost_usd": 0.001
    }


def execute_run(
    project_id: str,
    dataset_id: str,
    target_id: Optional[str] = None,
    level: int = 1,
    executor: str = "fake",  # fake, cached, live
    baseline_run_id: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
    triggered_by: str = "manual"
) -> Dict[str, Any]:
    """Execute an evaluation run and persist results."""
    run_config = config or {}

    with get_db_context() as db:
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
        if not dataset:
            raise ValueError(f"Dataset {dataset_id} not found")

        cases = db.query(Case).filter(Case.dataset_id == dataset_id, Case.active == True).all()
        target = db.query(Target).filter(Target.id == target_id).first() if target_id else None

        eval_run = EvalRun(
            project_id=project_id,
            dataset_id=dataset_id,
            target_id=target_id,
            level=level,
            executor=executor,
            config=run_config,
            app_version=run_config.get("app_version", "1.0.0"),
            status="running",
            baseline_run_id=baseline_run_id,
            triggered_by=triggered_by,
            started_at=datetime.now(timezone.utc)
        )
        db.add(eval_run)
        db.flush()

        results_list = []
        scenario_counts: Dict[str, Dict[str, int]] = {}
        total_passed = 0
        total_cost = 0.0
        total_latency = 0

        for case in cases:
            # 1. Fetch or simulate output
            start_t = time.perf_counter()
            if executor == "fake":
                output = generate_fake_output(case.input, case.scenario)
            elif executor == "live" and target:
                kind = target.kind
                cfg = target.config
                if kind == "http":
                    output = call_http_target(cfg.get("url", ""), case.input)
                elif kind == "cli":
                    output = call_cli_target(cfg.get("command", ""), case.input)
                elif kind == "python":
                    output = call_python_target(cfg.get("callable", ""), case.input)
                else:
                    output = {"text": "Target kind unsupported"}
            else:
                output = generate_fake_output(case.input, case.scenario)

            latency = int((time.perf_counter() - start_t) * 1000)
            if "latency_ms" in output:
                latency = output["latency_ms"]
            cost = float(output.get("cost_usd", 0.0))

            # 2. Evaluate assertions
            assertion_results = []
            case_passed = True
            configured_assertions = case.assertions or [{"name": "has_citation", "params": {}}]
            
            for asst in configured_assertions:
                name = asst.get("name")
                params = asst.get("params", {})
                res = run_assertion(name, case=case.input, output=output, **params)
                assertion_results.append({
                    "name": name,
                    "passed": res.passed,
                    "detail": res.detail
                })
                if not res.passed:
                    case_passed = False

            if case_passed:
                total_passed += 1

            # Track scenario statistics
            scen = case.scenario
            if scen not in scenario_counts:
                scenario_counts[scen] = {"total": 0, "passed": 0}
            scenario_counts[scen]["total"] += 1
            if case_passed:
                scenario_counts[scen]["passed"] += 1

            total_cost += cost
            total_latency += latency

            eval_res = EvalResult(
                eval_run_id=eval_run.id,
                case_id=case.id,
                output=output,
                assertion_results=assertion_results,
                passed=case_passed,
                cost_usd=cost,
                latency_ms=latency
            )
            db.add(eval_res)
            results_list.append(eval_res)

        # 3. Compute summaries and deltas
        n_cases = len(cases)
        overall_pass_rate = round(total_passed / n_cases, 4) if n_cases > 0 else 0.0

        scenario_summary = {}
        for scen, counts in scenario_counts.items():
            scen_total = counts["total"]
            scen_passed = counts["passed"]
            scenario_summary[scen] = {
                "total": scen_total,
                "passed": scen_passed,
                "pass_rate": round(scen_passed / scen_total, 4) if scen_total > 0 else 0.0
            }

        # Compare with baseline if provided
        deltas = {}
        regressions_count = 0
        if baseline_run_id:
            baseline = db.query(EvalRun).filter(EvalRun.id == baseline_run_id).first()
            if baseline and baseline.summary and "scenarios" in baseline.summary:
                base_scens = baseline.summary["scenarios"]
                for scen, sdata in scenario_summary.items():
                    base_rate = base_scens.get(scen, {}).get("pass_rate", sdata["pass_rate"])
                    delta = round(sdata["pass_rate"] - base_rate, 4)
                    status = "no_change"
                    if delta < -0.05:
                        status = "regression"
                        regressions_count += 1
                    elif delta > 0.05:
                        status = "improvement"
                    deltas[scen] = {
                        "baseline": base_rate,
                        "candidate": sdata["pass_rate"],
                        "delta": delta,
                        "status": status,
                        "significance": "significant" if sdata["total"] >= 30 else "not enough data"
                    }

        summary = {
            "total_cases": n_cases,
            "passed_cases": total_passed,
            "pass_rate": overall_pass_rate,
            "total_cost_usd": round(total_cost, 6),
            "avg_latency_ms": round(total_latency / n_cases, 1) if n_cases > 0 else 0,
            "scenarios": scenario_summary,
            "deltas": deltas,
            "regressions_count": regressions_count
        }

        eval_run.status = "completed"
        eval_run.summary = summary
        eval_run.finished_at = datetime.now(timezone.utc)
        db.flush()

        return {
            "run_id": eval_run.id,
            "status": "completed",
            "summary": summary
        }
