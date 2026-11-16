"""Test demonstrating that deliberate prompt regressions are blocked by release gates."""

import pytest
from decimal import Decimal
from backend.app.db.session import get_db_context
from backend.app.db.models import Dataset, Case, Gate, EvalRun
from backend.evalkit.datasets.manager import create_dataset, add_cases_to_dataset
from backend.evalkit.runner.run import execute_run
from backend.evalkit.runner.gate import evaluate_gates_for_run


def test_regressed_prompt_blocked_by_gate():
    with get_db_context() as db:
        ds = create_dataset("prj-default-001", "regressed_test_suite", feature="qa", db=db)
        # Add cases that expect citations
        add_cases_to_dataset(ds.id, [
            {
                "scenario": "regressed_prompt",
                "input": {"query": "Explain test feature"},
                "expected": {},
                "assertions": [{"name": "has_citation", "params": {}}]
            }
            for _ in range(10)
        ], db=db)

        # Create a strict gate: threshold 0.95
        strict_gate = db.query(Gate).filter(Gate.name == "strict_qa_citation_gate").first()
        if not strict_gate:
            strict_gate = Gate(
                project_id="prj-default-001",
                name="strict_qa_citation_gate",
                scope={"feature": "qa", "scenario": "regressed_prompt"},
                metric="pass_rate",
                threshold=Decimal("0.95"),
                rule="min",
                level=1,
                blocking=True,
                reason="Enforces mandatory citations in QA output"
            )
            db.add(strict_gate)
            db.flush()

        ds_id = ds.id

    # Execute run with regressed prompt output
    run_res = execute_run(
        project_id="prj-default-001",
        dataset_id=ds_id,
        level=1,
        executor="fake"
    )

    all_passed, gate_reports = evaluate_gates_for_run(run_res["run_id"])

    # Must FAIL because regressed_prompt outputs have no citations
    assert not all_passed, "Expected gate to block release due to regression"
    failing_gate = [g for g in gate_reports if g["gate_name"] == "strict_qa_citation_gate"][0]
    assert failing_gate["status"] == "fail"
    assert failing_gate["observed"] == 0.0
