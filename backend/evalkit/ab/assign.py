"""Deterministic A/B Experiment Assignment (Level 3)."""

import hashlib
from typing import Any, Dict, List, Optional
from backend.app.db.session import get_db_context
from backend.app.db.models import Experiment, ExperimentAssignment


def get_variant_for_subject(project_id: str, experiment_key: str, subject_id: str) -> str:
    """Deterministically assign a subject to a variant using MD5 hashing."""
    with get_db_context() as db:
        # Check existing assignment
        exp = db.query(Experiment).filter(
            Experiment.project_id == project_id,
            Experiment.key == experiment_key
        ).first()
        if not exp:
            return "control"

        existing = db.query(ExperimentAssignment).filter(
            ExperimentAssignment.experiment_id == exp.id,
            ExperimentAssignment.subject_id == subject_id
        ).first()
        if existing:
            return existing.variant

        variants = exp.variants or [{"name": "control", "weight": 50}, {"name": "variant_b", "weight": 50}]
        total_weight = sum(v.get("weight", 50) for v in variants)

        # Hash subject_id + key
        hash_val = int(hashlib.md5(f"{experiment_key}:{subject_id}".encode("utf-8")).hexdigest(), 16)
        bucket = hash_val % total_weight

        current = 0
        chosen = variants[0]["name"]
        for v in variants:
            current += v.get("weight", 50)
            if bucket < current:
                chosen = v["name"]
                break

        assignment = ExperimentAssignment(
            experiment_id=exp.id,
            subject_id=subject_id,
            variant=chosen
        )
        db.add(assignment)
        return chosen
