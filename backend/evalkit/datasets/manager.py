"""Dataset Loader, Synthetic Case Generator, and Trace Curator."""

import json
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session
from backend.app.db.session import get_db_context
from backend.app.db.models import Dataset, Case, Trace
from backend.evalkit.llm.client import ChatModel


def create_dataset(project_id: str, name: str, feature: str, description: str = "", is_golden: bool = False, holdout: bool = False, db: Optional[Session] = None) -> Dataset:
    def _create(session: Session):
        ds = session.query(Dataset).filter(Dataset.project_id == project_id, Dataset.name == name).first()
        if not ds:
            ds = Dataset(
                project_id=project_id,
                name=name,
                feature=feature,
                description=description,
                is_golden=is_golden,
                holdout=holdout
            )
            session.add(ds)
            session.flush()
        return ds

    if db:
        return _create(db)
    with get_db_context() as s:
        return _create(s)


def add_cases_to_dataset(dataset_id: str, raw_cases: List[Dict[str, Any]], source: str = "manual", db: Optional[Session] = None) -> int:
    def _add(session: Session):
        added = 0
        for item in raw_cases:
            c = Case(
                dataset_id=dataset_id,
                scenario=item.get("scenario", "default"),
                input=item.get("input", {}),
                expected=item.get("expected"),
                assertions=item.get("assertions", [{"name": "has_citation", "params": {}}]),
                tags=item.get("tags", []),
                source=source,
                needs_review=item.get("needs_review", False),
                active=True
            )
            session.add(c)
            added += 1
        session.flush()
        return added

    if db:
        return _add(db)
    with get_db_context() as s:
        return _add(s)


def generate_synthetic_cases(
    dataset_id: str,
    feature: str,
    scenario: str,
    persona: str = "general_user",
    count: int = 10,
    model: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Generates synthetic cases via ChatModel and adds them to dataset."""
    llm = ChatModel(model=model, temperature=0.7)
    prompt = f"""Generate {count} distinct test queries for feature '{feature}' and scenario '{scenario}'.
Target user persona: {persona}.
Output format: JSON array of objects:
[
  {{
    "scenario": "{scenario}",
    "input": {{"query": "..."}},
    "expected": {{"answer": "..."}},
    "tags": ["{feature}", "{scenario}", "{persona}", "synthetic"]
  }}
]"""
    response_text = llm.complete(prompt, response_format="json")
    try:
        cases_data = json.loads(response_text)
        if isinstance(cases_data, dict) and "cases" in cases_data:
            cases_data = cases_data["cases"]
    except Exception:
        cases_data = [
            {
                "scenario": scenario,
                "input": {"query": f"Sample query {i} for {feature}/{scenario} ({persona})"},
                "expected": {"answer": f"Expected answer {i}"},
                "tags": [feature, scenario, persona, "synthetic"]
            }
            for i in range(1, count + 1)
        ]

    cases_list = []
    if isinstance(cases_data, list):
        for c in cases_data:
            if isinstance(c, dict):
                c["needs_review"] = True
                cases_list.append(c)
            elif isinstance(c, str):
                cases_list.append({
                    "scenario": scenario,
                    "input": {"query": c},
                    "expected": {},
                    "tags": [feature, scenario, "synthetic"],
                    "needs_review": True
                })
    else:
        cases_list = [
            {
                "scenario": scenario,
                "input": {"query": f"Sample query {i} for {feature}/{scenario} ({persona})"},
                "expected": {"answer": f"Expected answer {i}"},
                "tags": [feature, scenario, persona, "synthetic"],
                "needs_review": True
            }
            for i in range(1, count + 1)
        ]

    add_cases_to_dataset(dataset_id, cases_list, source="synthetic")
    return cases_list


def convert_trace_to_case(trace_id: str, dataset_id: str, scenario: Optional[str] = None, assertions: Optional[List[Dict[str, Any]]] = None) -> Case:
    """Converts a real trace (e.g. failure) into a test case."""
    with get_db_context() as db:
        trace = db.query(Trace).filter(Trace.id == trace_id).first()
        if not trace:
            raise ValueError(f"Trace {trace_id} not found")

        case = Case(
            dataset_id=dataset_id,
            scenario=scenario or trace.scenario or "edge_case",
            input=trace.input or {},
            expected={"ideal_output": trace.labels[0].ideal_output} if trace.labels and trace.labels[0].ideal_output else None,
            assertions=assertions or [{"name": "has_citation", "params": {}}],
            tags=(trace.tags or []) + ["from_trace"],
            source="real_trace",
            source_trace_id=trace.id,
            needs_review=False,
            active=True
        )
        db.add(case)
        db.flush()
        return case
