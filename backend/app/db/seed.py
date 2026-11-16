"""Seed script for EvalKit with default project, user, API key, tags, and prompts."""

import hashlib
from backend.app.db.session import init_db, get_db_context
from backend.app.db.models import (
    User, Project, ProjectMember, ApiKey, FailureTag, PromptVersion
)

DEFAULT_API_KEY = "ek_live_dev_test_key_001"

def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()

def seed_database() -> dict:
    """Populates the database with initial development & testing records."""
    init_db()
    with get_db_context() as db:
        # 1. Default User
        user = db.query(User).filter(User.email == "admin@evalkit.local").first()
        if not user:
            user = User(
                id="usr-admin-001",
                email="admin@evalkit.local",
                password_hash=hash_key("evalkit-admin"),
                display_name="Admin Evaluator"
            )
            db.add(user)
            db.flush()

        # 2. Default Project
        project = db.query(Project).filter(Project.name == "default").first()
        if not project:
            project = Project(
                id="prj-default-001",
                name="default",
                settings={
                    "retention_days": 90,
                    "judge_model": "inclusionai/ling-3.2-flash:free",
                    "available_models": [
                        "inclusionai/ling-3.2-flash:free",
                        "nvidia/nemotron-3.5-super-vl:free",
                        "nvidia/nemotron-3-ultra-550b-a55b:free",
                        "perplexity/pplx-decider-v1.3:free"
                    ],
                    "redaction_rules": ["api_key", "email", "bearer_token", "credit_card"]
                }
            )
            db.add(project)
            db.flush()
        else:
            project.settings = {
                "retention_days": 90,
                "judge_model": "inclusionai/ling-3.2-flash:free",
                "available_models": [
                    "inclusionai/ling-3.2-flash:free",
                    "nvidia/nemotron-3.5-super-vl:free",
                    "nvidia/nemotron-3-ultra-550b-a55b:free",
                    "perplexity/pplx-decider-v1.3:free"
                ],
                "redaction_rules": ["api_key", "email", "bearer_token", "credit_card"]
            }

        # 3. Project Member
        member = db.query(ProjectMember).filter(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == user.id
        ).first()
        if not member:
            member = ProjectMember(
                project_id=project.id,
                user_id=user.id,
                role="admin"
            )
            db.add(member)

        # 4. API Key
        key_record = db.query(ApiKey).filter(ApiKey.name == "Dev Test Key").first()
        if not key_record:
            key_record = ApiKey(
                id="key-dev-001",
                project_id=project.id,
                name="Dev Test Key",
                key_hash=hash_key(DEFAULT_API_KEY),
                scopes=["ingest", "run", "admin"]
            )
            db.add(key_record)

        # 5. Standard Failure Tags
        tags = [
            ("wrong_citation", "Generated answer cites incorrect or non-existent source index"),
            ("missed_retrieval", "Context retrieval failed to fetch relevant documents"),
            ("bad_tool_args", "Tool call arguments violate schema or contain invalid values"),
            ("hallucination", "Output presents false or unverified facts"),
            ("prompt_leak", "System instructions or hidden prompt details leaked in output"),
            ("formatting_error", "JSON or markdown output structure is malformed"),
            ("latency_spike", "Response took longer than SLA budget")
        ]
        for name, desc in tags:
            if not db.query(FailureTag).filter(FailureTag.project_id == project.id, FailureTag.name == name).first():
                db.add(FailureTag(project_id=project.id, name=name, description=desc))

        # 6. Baseline Prompt Versions
        judge_faithfulness = db.query(PromptVersion).filter(
            PromptVersion.project_id == project.id,
            PromptVersion.name == "judge_faithfulness",
            PromptVersion.version == 1
        ).first()
        if not judge_faithfulness:
            judge_faithfulness = PromptVersion(
                id="pv-faithfulness-001",
                project_id=project.id,
                name="judge_faithfulness",
                version=1,
                template="""You are an expert LLM evaluation judge assessing faithfulness and groundedness.
Evaluate whether the MODEL OUTPUT is strictly supported by the RETRIEVED CONTEXT.

INPUT QUERY: {input}
RETRIEVED CONTEXT: {context}
MODEL OUTPUT: {output}

CRITERIA:
1. Every factual statement in the output must be supported by the retrieved context.
2. If the context does not contain sufficient information, the model should acknowledge that.
3. Answer with a JSON object: {{"verdict": "good"|"bad", "critique": "<one-line concise critique>"}}""",
                variables=["input", "context", "output"],
                notes="Initial zero-shot / few-shot prompt for faithfulness judging"
            )
            db.add(judge_faithfulness)

        synth_prompt = db.query(PromptVersion).filter(
            PromptVersion.project_id == project.id,
            PromptVersion.name == "synth_cases",
            PromptVersion.version == 1
        ).first()
        if not synth_prompt:
            synth_prompt = PromptVersion(
                id="pv-synth-001",
                project_id=project.id,
                name="synth_cases",
                version=1,
                template="""Generate {count} realistic, challenging test cases for feature '{feature}' and scenario '{scenario}'.
Target Persona: {persona}

Format: Return a JSON array of objects:
[
  {
    "scenario": "{scenario}",
    "input": {"query": "..."},
    "expected": {"answer": "...", "must_contain": [...]},
    "tags": ["{feature}", "{scenario}", "{persona}"]
  }
]""",
                variables=["count", "feature", "scenario", "persona"],
                notes="Baseline prompt for synthetic case generation"
            )
            db.add(synth_prompt)

        # 7. Default Judge
        from backend.app.db.models import Judge
        judge = db.query(Judge).filter(Judge.name == "faithfulness_judge").first()
        if not judge:
            judge = Judge(
                id="judge-faithfulness-001",
                project_id=project.id,
                name="faithfulness_judge",
                criterion="faithfulness",
                prompt_version_id=judge_faithfulness.id,
                model="inclusionai/ling-3.2-flash:free",
                min_labels_required=50
            )
            db.add(judge)

    return {
        "status": "seeded",
        "project_id": "prj-default-001",
        "api_key": DEFAULT_API_KEY,
        "user_email": "admin@evalkit.local"
    }

if __name__ == "__main__":
    result = seed_database()
    print("Seeded database:", result)
