"""LLM Judge with Prompt Versioning and Verdict Caching."""

import json
import hashlib
from typing import Any, Dict, Optional
from backend.app.db.session import get_db_context
from backend.app.db.models import Judge, JudgeVerdict, PromptVersion, Trace
from backend.evalkit.llm.client import ChatModel


def compute_input_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class LLMJudge:
    def __init__(self, judge_id: str):
        self.judge_id = judge_id

    def evaluate_trace(self, trace_id: str) -> Dict[str, Any]:
        """Evaluate a trace with the judge, using cache when available."""
        with get_db_context() as db:
            judge = db.query(Judge).filter(Judge.id == self.judge_id).first()
            if not judge:
                raise ValueError(f"Judge {self.judge_id} not found")

            trace = db.query(Trace).filter(Trace.id == trace_id).first()
            if not trace:
                raise ValueError(f"Trace {trace_id} not found")

            pv = db.query(PromptVersion).filter(PromptVersion.id == judge.prompt_version_id).first()
            if not pv:
                raise ValueError("Prompt version not found")

            # Check cache
            raw_input_str = json.dumps(trace.input or "")
            input_hash = compute_input_hash(f"{judge.id}:{pv.version}:{raw_input_str}:{json.dumps(trace.output or '')}")

            cached = db.query(JudgeVerdict).filter(
                JudgeVerdict.judge_id == judge.id,
                JudgeVerdict.trace_id == trace.id
            ).first()
            if cached and cached.input_hash == input_hash:
                return {
                    "verdict": cached.verdict,
                    "critique": cached.critique,
                    "cached": True
                }

            # Build prompt safely
            prompt = pv.template
            prompt = prompt.replace("{input}", json.dumps(trace.input or {}))
            prompt = prompt.replace("{context}", json.dumps(trace.spans[0].attributes if trace.spans else {}))
            prompt = prompt.replace("{output}", json.dumps(trace.output or {}))

            # Query model
            llm = ChatModel(model=judge.model, temperature=0.0)
            response_text = llm.complete(prompt, response_format="json")

            try:
                parsed = json.loads(response_text)
                verdict = parsed.get("verdict", "good")
                critique = parsed.get("critique", "Evaluation complete")
            except Exception:
                verdict = "good" if "good" in response_text.lower() else "bad"
                critique = response_text[:200]

            # Save verdict
            if not cached:
                verdict_rec = JudgeVerdict(
                    judge_id=judge.id,
                    trace_id=trace.id,
                    verdict=verdict,
                    critique=critique,
                    input_hash=input_hash
                )
                db.add(verdict_rec)
            else:
                cached.verdict = verdict
                cached.critique = critique
                cached.input_hash = input_hash

            return {
                "verdict": verdict,
                "critique": critique,
                "cached": False
            }
