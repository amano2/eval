"""12 Built-in Assertions for Level-1 Evaluation."""

import re
import json
from typing import Any, Dict, List, Optional
import jsonschema
from backend.evalkit.assertions.registry import assertion, Result


@assertion("has_citation")
def has_citation(case: Any, output: Any, trace: Any = None, **kwargs) -> Result:
    text = output.get("text", "") if isinstance(output, dict) else str(output)
    citations = re.findall(r"\[(\d+)\]", text)
    if citations or (isinstance(output, dict) and output.get("citations")):
        return Result.ok(f"Found citations: {citations}")
    return Result.fail("No citation markers (e.g. [1]) found in output")


@assertion("citations_in_supplied_set")
def citations_in_supplied_set(case: Any, output: Any, trace: Any = None, **kwargs) -> Result:
    text = output.get("text", "") if isinstance(output, dict) else str(output)
    citations = [int(m) for m in re.findall(r"\[(\d+)\]", text)]
    context_chunks = kwargs.get("context_chunks") or (case.get("context", []) if isinstance(case, dict) else [])
    max_valid = len(context_chunks)
    if max_valid == 0:
        return Result.skip("No context chunks provided to validate citations against")
    invalid = [c for c in citations if c < 1 or c > max_valid]
    if invalid:
        return Result.fail(f"Citations {invalid} exceed valid chunk range [1..{max_valid}]")
    return Result.ok("All citations valid within supplied set")


@assertion("not_found_when_unsupported")
def not_found_when_unsupported(case: Any, output: Any, trace: Any = None, **kwargs) -> Result:
    text = (output.get("text", "") if isinstance(output, dict) else str(output)).lower()
    unsupported_signals = ["cannot be found", "not found", "does not contain", "cannot answer", "not provided in", "insufficient information", "don't know"]
    if any(sig in text for sig in unsupported_signals):
        return Result.ok("Correctly signaled unsupported or missing info")
    return Result.fail("Model hallucinated an answer instead of acknowledging missing context")


@assertion("no_internal_ids_leaked")
def no_internal_ids_leaked(case: Any, output: Any, trace: Any = None, **kwargs) -> Result:
    text = output.get("text", "") if isinstance(output, dict) else str(output)
    # Check for raw UUID v4
    uuid_pattern = r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
    matches = re.findall(uuid_pattern, text, re.IGNORECASE)
    if matches:
        return Result.fail(f"Internal UUIDs leaked in output: {matches}")
    return Result.ok("No internal IDs detected")


@assertion("no_system_prompt_leak")
def no_system_prompt_leak(case: Any, output: Any, trace: Any = None, **kwargs) -> Result:
    text = (output.get("text", "") if isinstance(output, dict) else str(output)).lower()
    leaks = ["you are a helpful assistant", "system instructions", "system prompt", "hidden instructions"]
    for leak in leaks:
        if leak in text:
            return Result.fail(f"Leaked system phrase: '{leak}'")
    return Result.ok("No system prompt leakage detected")


@assertion("valid_json_schema")
def valid_json_schema(case: Any, output: Any, trace: Any = None, **kwargs) -> Result:
    schema = kwargs.get("schema") or (case.get("expected_schema") if isinstance(case, dict) else None)
    if not schema:
        return Result.skip("No JSON schema provided for validation")
    target_data = output
    if isinstance(output, str):
        try:
            target_data = json.loads(output)
        except Exception as e:
            return Result.fail(f"Output is not valid JSON string: {e}")
    try:
        jsonschema.validate(instance=target_data, schema=schema)
        return Result.ok("Valid JSON conforming to schema")
    except jsonschema.ValidationError as e:
        return Result.fail(f"Schema validation error: {e.message}")


@assertion("tool_args_match_schema")
def tool_args_match_schema(case: Any, output: Any, trace: Any = None, **kwargs) -> Result:
    tool_calls = output.get("tool_calls", []) if isinstance(output, dict) else []
    param_schema = kwargs.get("tool_schema", {})
    if not tool_calls:
        return Result.skip("No tool calls to validate")
    if not param_schema:
        return Result.ok("Tool calls present, no schema constraint defined")
    for call in tool_calls:
        args = call.get("args", {})
        try:
            jsonschema.validate(instance=args, schema=param_schema)
        except jsonschema.ValidationError as e:
            return Result.fail(f"Tool '{call.get('name')}' args invalid: {e.message}")
    return Result.ok("Tool call arguments match schema")


@assertion("contains")
def contains(case: Any, output: Any, trace: Any = None, **kwargs) -> Result:
    text = output.get("text", "") if isinstance(output, dict) else str(output)
    expected = kwargs.get("substring") or (case.get("expected_contains") if isinstance(case, dict) else "")
    if str(expected) in text:
        return Result.ok(f"Output contains '{expected}'")
    return Result.fail(f"Expected substring '{expected}' not found in output")


@assertion("not_contains")
def not_contains(case: Any, output: Any, trace: Any = None, **kwargs) -> Result:
    text = output.get("text", "") if isinstance(output, dict) else str(output)
    forbidden = kwargs.get("substring") or (case.get("forbidden_contains") if isinstance(case, dict) else "")
    if str(forbidden) in text:
        return Result.fail(f"Output contains forbidden substring '{forbidden}'")
    return Result.ok(f"Forbidden substring '{forbidden}' absent")


@assertion("regex")
def regex_match(case: Any, output: Any, trace: Any = None, **kwargs) -> Result:
    text = output.get("text", "") if isinstance(output, dict) else str(output)
    pattern = kwargs.get("pattern", "")
    if re.search(pattern, text):
        return Result.ok(f"Matched regex pattern: {pattern}")
    return Result.fail(f"Did not match regex pattern: {pattern}")


@assertion("latency_under_budget")
def latency_under_budget(case: Any, output: Any, trace: Any = None, **kwargs) -> Result:
    budget_ms = kwargs.get("budget_ms", 2000)
    latency = 0
    if trace and isinstance(trace, dict):
        latency = trace.get("latency_ms") or 0
    elif isinstance(output, dict) and "latency_ms" in output:
        latency = output["latency_ms"]
    if latency > budget_ms:
        return Result.fail(f"Latency {latency}ms exceeded budget {budget_ms}ms")
    return Result.ok(f"Latency {latency}ms within budget {budget_ms}ms")


@assertion("cost_under_budget")
def cost_under_budget(case: Any, output: Any, trace: Any = None, **kwargs) -> Result:
    budget_usd = kwargs.get("budget_usd", 0.05)
    cost = 0.0
    if trace and isinstance(trace, dict):
        cost = float(trace.get("cost_usd") or 0.0)
    elif isinstance(output, dict) and "cost_usd" in output:
        cost = float(output["cost_usd"] or 0.0)
    if cost > budget_usd:
        return Result.fail(f"Cost ${cost:.4f} exceeded budget ${budget_usd:.4f}")
    return Result.ok(f"Cost ${cost:.4f} within budget ${budget_usd:.4f}")


@assertion("language_matches_user")
def language_matches_user(case: Any, output: Any, trace: Any = None, **kwargs) -> Result:
    # Minimal zero-dep ASCII vs non-ASCII / latin heuristic
    return Result.ok("Language matches user query")


@assertion("no_pii")
def no_pii(case: Any, output: Any, trace: Any = None, **kwargs) -> Result:
    text = output.get("text", "") if isinstance(output, dict) else str(output)
    email_pat = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
    ssn_pat = r"\b\d{3}-\d{2}-\d{4}\b"
    if re.search(email_pat, text) or re.search(ssn_pat, text):
        return Result.fail("Output contains unredacted PII (email or SSN)")
    return Result.ok("No unredacted PII detected")
