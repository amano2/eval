"""Sample RAG application instrumented with the EvalKit Python SDK."""

import time
from typing import Dict, Any, List
from sdk.python.evalkit import trace, span, guard

# Simulated document store
KNOWLEDGE_BASE = {
    "evalkit": "EvalKit provides Level-1 assertions, human review queues, and LLM judge alignment [1].",
    "retrieval": "Dense vector retrieval uses bi-encoders for semantic passage matching [2].",
    "guardrails": "Runtime guards perform 1-shot retry using critique feedback to guarantee format safety [3]."
}


def retrieve_documents(query: str) -> List[str]:
    with span("retrieve_docs", kind="retrieval", input_data={"query": query}) as s:
        results = []
        for key, doc in KNOWLEDGE_BASE.items():
            if key in query.lower():
                results.append(doc)
        s.set_output({"retrieved_count": len(results), "docs": results})
        return results


def generate_answer(query: str, docs: List[str], prompt_version: str = "v1") -> str:
    with span("llm_generate", kind="model", attributes={"model": "mock-llm-v1", "version": prompt_version}) as s:
        if not docs:
            out = "The requested information cannot be found in the provided documentation."
        elif prompt_version == "v2_regressed":
            # Intentional regression: hallucinates and omits citations!
            out = "Here is an answer without citations or grounding."
        else:
            # Good response with citation [1]
            out = f"According to documentation, {docs[0]} [1]"

        s.set_output(out)
        return out


def ask(query: str, prompt_version: str = "v1") -> Dict[str, Any]:
    """Top-level ask endpoint instrumented with @trace and runtime guard."""
    with trace(feature="qa", scenario="answer_in_docs" if "evalkit" in query.lower() else "answer_not_in_docs", prompt_version=prompt_version) as t:
        docs = retrieve_documents(query)
        answer = generate_answer(query, docs, prompt_version=prompt_version)

        # Optional runtime guard
        try:
            checked_answer = guard("no_internal_ids_leaked", answer)
        except Exception:
            checked_answer = answer

        t.set_output({"answer": checked_answer, "docs_count": len(docs)})
        t.add_tokens(prompt=20, completion=15, cost=0.0005)
        return {"answer": checked_answer, "docs": docs}


if __name__ == "__main__":
    print("Running sample app query:")
    res = ask("Tell me about evalkit features")
    print("Result:", res)
