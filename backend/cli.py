"""EvalKit Command Line Interface (CLI)."""

import os
import sys

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import json
import argparse
from typing import Any

from backend.app.db.session import init_db, get_db_context
from backend.app.db.models import Dataset, Case, EvalRun
from backend.evalkit.runner.run import execute_run
from backend.evalkit.runner.gate import evaluate_gates_for_run
from backend.evalkit.judges.alignment import compute_judge_alignment
from backend.evalkit.datasets.manager import generate_synthetic_cases, create_dataset
from backend.evalkit.export.finetune import export_finetune_data


def run_command(args):
    init_db()
    with get_db_context() as db:
        # Resolve dataset
        ds = db.query(Dataset).filter((Dataset.id == args.dataset) | (Dataset.name == args.dataset)).first()
        if not ds:
            # Auto-create if not exists for quick CLI usage
            ds = create_dataset("prj-default-001", args.dataset, feature="qa", is_golden=True, db=db)
            # Add dummy cases if empty
            if not ds.cases:
                from backend.evalkit.datasets.manager import add_cases_to_dataset
                add_cases_to_dataset(ds.id, [
                    {"scenario": "answer_in_docs", "input": {"q": "sample 1"}, "assertions": [{"name": "has_citation"}]},
                    {"scenario": "answer_not_in_docs", "input": {"q": "sample 2"}, "assertions": [{"name": "not_found_when_unsupported"}]}
                ], db=db)
        dataset_id = ds.id

    res = execute_run(
        project_id="prj-default-001",
        dataset_id=dataset_id,
        level=args.level,
        executor=args.executor,
        baseline_run_id=args.baseline
    )

    if args.json:
        print(json.dumps(res, indent=2))
        return

    summary = res["summary"]
    print(f"\nEvalKit Run Finished: {res['run_id']}")
    print("-" * 50)
    for scen, sdata in summary.get("scenarios", {}).items():
        status = "PASS" if sdata["pass_rate"] >= 0.90 else "FAIL"
        print(f"{scen:<25} {sdata['passed']}/{sdata['total']} ({sdata['pass_rate']*100:.1f}%)   {status}")
    print("-" * 50)
    print(f"Overall Pass Rate: {summary['pass_rate']*100:.1f}%")

    # Evaluate gates
    passed, gate_reports = evaluate_gates_for_run(res["run_id"])
    if not passed:
        print("\nGate: FAILED (Release Blocked).")
        for g in gate_reports:
            if g["status"] == "fail":
                print(f"  [X] {g['gate_name']}: observed {g['observed']} < threshold {g['threshold']}")
        sys.exit(1)
    else:
        print("\nGate: PASSED (All release criteria met).")
        sys.exit(0)


def gate_command(args):
    init_db()
    try:
        run_id = args.run_id
        if run_id == "latest":
            with get_db_context() as db:
                latest = db.query(EvalRun).order_by(EvalRun.created_at.desc()).first()
                if not latest:
                    print("Error: No eval runs found.", file=sys.stderr)
                    sys.exit(2)
                run_id = latest.id

        passed, gate_reports = evaluate_gates_for_run(run_id)
        if args.json:
            print(json.dumps({"passed": passed, "gates": gate_reports}, indent=2))
        else:
            print(f"Gate Evaluation for Run: {run_id}")
            for g in gate_reports:
                icon = "[PASS]" if g["status"] == "pass" else "[FAIL]"
                print(f"  {icon} {g['gate_name']}: {g['scenario']} (observed: {g['observed']}, threshold: {g['threshold']})")
            if not passed:
                print("\nResult: GATE FAILED")
                sys.exit(1)
            else:
                print("\nResult: GATE PASSED")
                sys.exit(0)
    except Exception as e:
        print(f"Operational error: {e}", file=sys.stderr)
        sys.exit(2)


def align_command(args):
    init_db()
    report = compute_judge_alignment(args.judge_id)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"\nJudge Alignment Report for: {report['judge_name']}")
        print(f"Total Human Labeled Traces: {report['total_labeled']} (Minimum Required: {report['min_labels_required']})")
        print(f"Status: {'ALIGNED' if report['is_aligned'] else 'UNALIGNED (Needs more labels or higher precision/recall)'}")
        print("\nClass 'bad' (Failures - Positive Class):")
        print(f"  Precision: {report['metrics']['bad_class']['precision']:.3f}")
        print(f"  Recall:    {report['metrics']['bad_class']['recall']:.3f}")
        print("\nClass 'good':")
        print(f"  Precision: {report['metrics']['good_class']['precision']:.3f}")
        print(f"  Recall:    {report['metrics']['good_class']['recall']:.3f}")
        cm = report["confusion_matrix"]
        print(f"\nConfusion Matrix: TP={cm['tp']}  FP={cm['fp']}  FN={cm['fn']}  TN={cm['tn']}")
        print(f"Disagreements count: {len(report['disagreements'])}")


def generate_command(args):
    init_db()
    with get_db_context() as db:
        ds = db.query(Dataset).filter((Dataset.id == args.dataset) | (Dataset.name == args.dataset)).first()
        if not ds:
            ds = create_dataset("prj-default-001", args.dataset, feature=args.feature, db=db)
        dataset_id = ds.id
        dataset_name = ds.name
    cases = generate_synthetic_cases(dataset_id, feature=args.feature, scenario=args.scenario, count=args.count)
    print(f"Generated {len(cases)} synthetic cases in dataset '{dataset_name}'.")


def export_command(args):
    init_db()
    res = export_finetune_data("prj-default-001", args.output)
    print(f"Exported {res['exported_count']} high-quality examples to {res['file_path']}")


def main():
    parser = argparse.ArgumentParser(prog="evalkit", description="EvalKit CLI")
    subparsers = parser.add_subparsers(dest="command")

    # Run
    p_run = subparsers.add_parser("run", help="Execute an evaluation run")
    p_run.add_argument("--dataset", required=True, help="Dataset name or ID")
    p_run.add_argument("--target", default="http://localhost:8000", help="Target URL or command")
    p_run.add_argument("--level", type=int, default=1, help="Eval level (1, 2, 3)")
    p_run.add_argument("--executor", default="fake", choices=["fake", "cached", "live"], help="Execution mode")
    p_run.add_argument("--baseline", help="Baseline run ID for comparison")
    p_run.add_argument("--json", action="store_true", help="Output JSON")
    p_run.set_defaults(func=run_command)

    # Gate
    p_gate = subparsers.add_parser("gate", help="Check gate status for a run")
    p_gate.add_argument("--run-id", default="latest", help="Eval run ID or 'latest'")
    p_gate.add_argument("--json", action="store_true", help="Output JSON")
    p_gate.set_defaults(func=gate_command)

    # Align
    p_align = subparsers.add_parser("align", help="Check judge alignment metrics")
    p_align.add_argument("--judge-id", default="judge-align-test", help="Judge ID")
    p_align.add_argument("--json", action="store_true", help="Output JSON")
    p_align.set_defaults(func=align_command)

    # Generate
    p_gen = subparsers.add_parser("generate", help="Generate synthetic test cases")
    p_gen.add_argument("--dataset", default="default-golden", help="Target dataset")
    p_gen.add_argument("--feature", default="qa", help="Feature name")
    p_gen.add_argument("--scenario", default="answer_in_docs", help="Scenario name")
    p_gen.add_argument("--count", type=int, default=5, help="Number of cases")
    p_gen.set_defaults(func=generate_command)

    # Export
    p_exp = subparsers.add_parser("export", help="Export fine-tune data")
    p_exp.add_argument("--output", default="./exports/finetune.jsonl", help="Output JSONL path")
    p_exp.set_defaults(func=export_command)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
