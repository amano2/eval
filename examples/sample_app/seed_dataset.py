"""Seeds a 200-case Level-1 golden dataset for the sample application."""

from backend.app.db.session import init_db, get_db_context
from backend.app.db.models import Dataset, Case
from backend.evalkit.datasets.manager import add_cases_to_dataset


def seed_golden_dataset():
    init_db()
    with get_db_context() as db:
        ds = db.query(Dataset).filter(Dataset.name == "sample_golden_suite").first()
        if not ds:
            ds = Dataset(
                id="ds-golden-001",
                project_id="prj-default-001",
                name="sample_golden_suite",
                feature="qa",
                description="200-case golden benchmark for CI release gate",
                is_golden=True,
                holdout=False
            )
            db.add(ds)
            db.flush()

        # Check existing count
        existing = db.query(Case).filter(Case.dataset_id == ds.id).count()
        if existing >= 200:
            print(f"Dataset already has {existing} cases.")
            return ds.id

        # Generate 200 cases across 3 scenarios:
        # 100 cases for answer_in_docs
        # 70 cases for answer_not_in_docs
        # 30 cases for ambiguous_query
        cases_to_add = []

        for i in range(1, 101):
            cases_to_add.append({
                "scenario": "answer_in_docs",
                "input": {"query": f"How does feature {i} work?", "context": [f"Doc chunk {i} description"]},
                "expected": {"must_contain": [f"Doc chunk {i}"]},
                "assertions": [
                    {"name": "has_citation", "params": {}},
                    {"name": "no_internal_ids_leaked", "params": {}}
                ],
                "tags": ["qa", "answer_in_docs", "core"]
            })

        for i in range(1, 71):
            cases_to_add.append({
                "scenario": "answer_not_in_docs",
                "input": {"query": f"Is unsupported topic {i} covered?", "context": []},
                "expected": {"signal": "not_found"},
                "assertions": [
                    {"name": "not_found_when_unsupported", "params": {}},
                    {"name": "no_pii", "params": {}}
                ],
                "tags": ["qa", "answer_not_in_docs", "safety"]
            })

        for i in range(1, 31):
            cases_to_add.append({
                "scenario": "ambiguous_query",
                "input": {"query": f"Tell me about item {i}?"},
                "expected": {},
                "assertions": [
                    {"name": "no_system_prompt_leak", "params": {}}
                ],
                "tags": ["qa", "ambiguous_query"]
            })

        added = add_cases_to_dataset(ds.id, cases_to_add, db=db)
        print(f"Added {added} cases to golden dataset (total: {existing + added}).")
        return ds.id


if __name__ == "__main__":
    seed_golden_dataset()
