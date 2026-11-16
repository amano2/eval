"""Release Gate Evaluator and Policy Enforcement."""

from typing import Any, Dict, List, Tuple
from decimal import Decimal
from backend.app.db.session import get_db_context
from backend.app.db.models import EvalRun, Gate, GateResult


def evaluate_gates_for_run(run_id: str) -> Tuple[bool, List[Dict[str, Any]]]:
    """Evaluates all applicable gates for an eval run.
    Returns (all_passed: bool, gate_details: List[dict])."""
    with get_db_context() as db:
        run = db.query(EvalRun).filter(EvalRun.id == run_id).first()
        if not run:
            raise ValueError(f"Run {run_id} not found")

        summary = run.summary or {}
        overall_pass_rate = summary.get("pass_rate", 0.0)
        scenario_data = summary.get("scenarios", {})

        # Find matching gates for this project / dataset
        gates = db.query(Gate).filter(Gate.project_id == run.project_id).all()
        if not gates:
            # If no custom gates exist, use a default 0.90 overall pass-rate rule
            default_gate = Gate(
                project_id=run.project_id,
                name="default_pass_rate_gate",
                scope={"feature": "all"},
                metric="pass_rate",
                threshold=Decimal("0.90"),
                rule="min",
                level=run.level,
                blocking=True,
                reason="Default safety gate: minimum 90% pass rate"
            )
            db.add(default_gate)
            db.flush()
            gates = [default_gate]

        all_passed = True
        gate_reports = []

        for gate in gates:
            scope = gate.scope or {}
            target_scenario = scope.get("scenario")
            threshold = float(gate.threshold)

            # Determine observed metric
            if target_scenario and target_scenario in scenario_data:
                observed = scenario_data[target_scenario].get("pass_rate", 0.0)
            else:
                observed = overall_pass_rate

            passed = False
            baseline_val = None

            if gate.rule == "min":
                passed = (observed >= threshold)
            elif gate.rule == "max":
                passed = (observed <= threshold)
            elif gate.rule == "no_significant_drop":
                # Check regression against baseline
                deltas = summary.get("deltas", {})
                if target_scenario and target_scenario in deltas:
                    delta = deltas[target_scenario].get("delta", 0.0)
                    baseline_val = deltas[target_scenario].get("baseline", observed)
                    passed = (delta >= -0.05)
                else:
                    passed = (observed >= threshold)

            status = "pass" if passed else "fail"
            if not passed and gate.blocking:
                all_passed = False

            # Persist gate result
            gate_res = db.query(GateResult).filter(
                GateResult.eval_run_id == run.id,
                GateResult.gate_id == gate.id
            ).first()
            if not gate_res:
                gate_res = GateResult(
                    eval_run_id=run.id,
                    gate_id=gate.id,
                    status=status,
                    observed=Decimal(str(round(observed, 4))),
                    baseline=Decimal(str(round(baseline_val, 4))) if baseline_val is not None else None,
                    detail={"rule": gate.rule, "threshold": threshold, "scenario": target_scenario}
                )
                db.add(gate_res)
            else:
                gate_res.status = status
                gate_res.observed = Decimal(str(round(observed, 4)))

            gate_reports.append({
                "gate_name": gate.name,
                "rule": gate.rule,
                "scenario": target_scenario or "overall",
                "threshold": threshold,
                "observed": observed,
                "status": status,
                "blocking": gate.blocking
            })

        return all_passed, gate_reports
