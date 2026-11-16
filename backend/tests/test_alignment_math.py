"""Frozen fixture test for judge alignment precision, recall, and confusion matrix."""

import pytest
from backend.app.db.session import get_db_context
from backend.app.db.models import Project, Judge, PromptVersion, Trace, HumanLabel, JudgeVerdict
from backend.evalkit.judges.alignment import compute_judge_alignment


def test_frozen_alignment_math():
    with get_db_context() as db:
        # Create test project & judge
        prj = db.query(Project).filter(Project.id == "prj-test-alignment").first()
        if not prj:
            prj = Project(id="prj-test-alignment", name="test-alignment-prj")
            db.add(prj)
            db.flush()

        pv = db.query(PromptVersion).filter(PromptVersion.id == "pv-align-test").first()
        if not pv:
            pv = PromptVersion(id="pv-align-test", project_id=prj.id, name="test_pv", version=1, template="{input}")
            db.add(pv)
            db.flush()

        judge = db.query(Judge).filter(Judge.id == "judge-align-test").first()
        if not judge:
            judge = Judge(
                id="judge-align-test",
                project_id=prj.id,
                name="test_faithfulness",
                criterion="faithfulness",
                prompt_version_id=pv.id,
                model="test-model",
                min_labels_required=20
            )
            db.add(judge)
            db.flush()

        # Generate a known fixture of 20 traces:
        # 10 truly bad (human: bad), 10 truly good (human: good)
        # Judge:
        # For the 10 bad: 8 marked bad (TP=8), 2 marked good (FN=2)
        # For the 10 good: 9 marked good (TN=9), 1 marked bad (FP=1)
        # Expected:
        # TP = 8, FP = 1, FN = 2, TN = 9
        # precision_bad = 8 / (8 + 1) = 8/9 = 0.889
        # recall_bad = 8 / (8 + 2) = 8/10 = 0.800
        # precision_good = 9 / (9 + 2) = 9/11 = 0.818
        # recall_good = 9 / (9 + 1) = 9/10 = 0.900
        for i in range(20):
            trace_id = f"tr-align-{i:02d}"
            t = db.query(Trace).filter(Trace.id == trace_id).first()
            if not t:
                t = Trace(id=trace_id, project_id=prj.id, input={"q": i})
                db.add(t)
                db.flush()

            # Human label
            human_label_val = "bad" if i < 10 else "good"
            hl = db.query(HumanLabel).filter(HumanLabel.trace_id == trace_id).first()
            if not hl:
                hl = HumanLabel(
                    project_id=prj.id,
                    trace_id=trace_id,
                    labeler_id="usr-admin-001",
                    criterion="faithfulness",
                    label=human_label_val
                )
                db.add(hl)

            # Judge verdict
            if i < 8:
                judge_verdict_val = "bad"  # TP
            elif i < 10:
                judge_verdict_val = "good"  # FN
            elif i < 11:
                judge_verdict_val = "bad"  # FP
            else:
                judge_verdict_val = "good"  # TN

            jv = db.query(JudgeVerdict).filter(JudgeVerdict.judge_id == judge.id, JudgeVerdict.trace_id == trace_id).first()
            if not jv:
                jv = JudgeVerdict(
                    judge_id=judge.id,
                    trace_id=trace_id,
                    verdict=judge_verdict_val,
                    critique="Test critique",
                    input_hash=f"hash-{i}"
                )
                db.add(jv)

    # Compute alignment
    report = compute_judge_alignment("judge-align-test")
    cm = report["confusion_matrix"]
    assert cm["tp"] == 8
    assert cm["fp"] == 1
    assert cm["fn"] == 2
    assert cm["tn"] == 9

    bad_metrics = report["metrics"]["bad_class"]
    good_metrics = report["metrics"]["good_class"]

    assert bad_metrics["precision"] == 0.889
    assert bad_metrics["recall"] == 0.800
    assert good_metrics["precision"] == 0.818
    assert good_metrics["recall"] == 0.900

    # 3 disagreements in total (2 FN + 1 FP)
    assert len(report["disagreements"]) == 3
