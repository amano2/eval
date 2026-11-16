"""Judge Alignment Reporting (Precision, Recall, Confusion Matrix, Disagreements)."""

from typing import Any, Dict, List, Optional
from collections import defaultdict
from backend.app.db.session import get_db_context
from backend.app.db.models import Judge, JudgeVerdict, HumanLabel, Trace


def compute_judge_alignment(judge_id: str) -> Dict[str, Any]:
    """Computes precision and recall for both classes ('bad' as positive),
    confusion matrix, and extracts disagreements."""
    with get_db_context() as db:
        judge = db.query(Judge).filter(Judge.id == judge_id).first()
        if not judge:
            raise ValueError(f"Judge {judge_id} not found")

        # 1. Fetch all human labels for this criterion
        human_labels = db.query(HumanLabel).filter(
            HumanLabel.project_id == judge.project_id,
            HumanLabel.criterion == judge.criterion
        ).all()

        # Majority vote per trace
        trace_votes: Dict[str, List[str]] = defaultdict(list)
        trace_human_critiques: Dict[str, str] = {}
        for hl in human_labels:
            trace_votes[hl.trace_id].append(hl.label)
            if hl.critique:
                trace_human_critiques[hl.trace_id] = hl.critique

        ground_truth: Dict[str, str] = {}
        for trace_id, votes in trace_votes.items():
            bad_votes = sum(1 for v in votes if v == "bad")
            good_votes = len(votes) - bad_votes
            ground_truth[trace_id] = "bad" if bad_votes >= good_votes else "good"

        # 2. Fetch judge verdicts
        verdicts = db.query(JudgeVerdict).filter(JudgeVerdict.judge_id == judge.id).all()
        judge_map = {v.trace_id: v for v in verdicts}

        tp = fp = fn = tn = 0
        disagreements = []

        for trace_id, human_label in ground_truth.items():
            if trace_id not in judge_map:
                continue
            jv = judge_map[trace_id]
            judge_verdict = jv.verdict

            if judge_verdict == "bad" and human_label == "bad":
                tp += 1
            elif judge_verdict == "bad" and human_label == "good":
                fp += 1
                disagreements.append({
                    "trace_id": trace_id,
                    "human_label": human_label,
                    "human_critique": trace_human_critiques.get(trace_id, ""),
                    "judge_verdict": judge_verdict,
                    "judge_critique": jv.critique
                })
            elif judge_verdict == "good" and human_label == "bad":
                fn += 1
                disagreements.append({
                    "trace_id": trace_id,
                    "human_label": human_label,
                    "human_critique": trace_human_critiques.get(trace_id, ""),
                    "judge_verdict": judge_verdict,
                    "judge_critique": jv.critique
                })
            elif judge_verdict == "good" and human_label == "good":
                tn += 1

        total_labeled = tp + fp + fn + tn

        # Precision & Recall: Class 'bad' (positive class)
        precision_bad = round(tp / (tp + fp), 3) if (tp + fp) > 0 else 0.0
        recall_bad = round(tp / (tp + fn), 3) if (tp + fn) > 0 else 0.0

        # Precision & Recall: Class 'good'
        precision_good = round(tn / (tn + fn), 3) if (tn + fn) > 0 else 0.0
        recall_good = round(tn / (tn + fp), 3) if (tn + fp) > 0 else 0.0

        is_aligned = (total_labeled >= judge.min_labels_required and precision_bad >= 0.85 and recall_bad >= 0.85)

        return {
            "judge_id": judge.id,
            "judge_name": judge.name,
            "criterion": judge.criterion,
            "total_labeled": total_labeled,
            "min_labels_required": judge.min_labels_required,
            "is_aligned": is_aligned,
            "confusion_matrix": {
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "tn": tn
            },
            "metrics": {
                "bad_class": {
                    "precision": precision_bad,
                    "recall": recall_bad
                },
                "good_class": {
                    "precision": precision_good,
                    "recall": recall_good
                }
            },
            "disagreements": disagreements
        }
