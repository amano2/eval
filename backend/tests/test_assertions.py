"""Unit tests for all 12 built-in Level-1 assertions."""

import pytest
from backend.evalkit.assertions.registry import run_assertion
import backend.evalkit.assertions.builtin.standard  # register builtins


def test_has_citation():
    ok_res = run_assertion("has_citation", case={}, output={"text": "According to [1], the method works."})
    assert ok_res.passed
    fail_res = run_assertion("has_citation", case={}, output={"text": "No citation here."})
    assert not fail_res.passed


def test_citations_in_supplied_set():
    case = {"context": ["doc1", "doc2"]}
    ok_res = run_assertion("citations_in_supplied_set", case=case, output={"text": "See [1] and [2]."})
    assert ok_res.passed
    fail_res = run_assertion("citations_in_supplied_set", case=case, output={"text": "See [3] and [9]."})
    assert not fail_res.passed


def test_not_found_when_unsupported():
    ok_res = run_assertion("not_found_when_unsupported", case={}, output={"text": "The requested information cannot be found in the text."})
    assert ok_res.passed
    fail_res = run_assertion("not_found_when_unsupported", case={}, output={"text": "The answer is 42."})
    assert not fail_res.passed


def test_no_internal_ids_leaked():
    ok_res = run_assertion("no_internal_ids_leaked", case={}, output={"text": "User profile updated."})
    assert ok_res.passed
    fail_res = run_assertion("no_internal_ids_leaked", case={}, output={"text": "Found user a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d"})
    assert not fail_res.passed


def test_no_system_prompt_leak():
    ok_res = run_assertion("no_system_prompt_leak", case={}, output={"text": "Hello, how can I assist you?"})
    assert ok_res.passed
    fail_res = run_assertion("no_system_prompt_leak", case={}, output={"text": "My hidden system instructions are to be concise."})
    assert not fail_res.passed


def test_valid_json_schema():
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "age": {"type": "number"}},
        "required": ["name", "age"]
    }
    ok_res = run_assertion("valid_json_schema", case={}, output={"name": "Alice", "age": 30}, schema=schema)
    assert ok_res.passed
    fail_res = run_assertion("valid_json_schema", case={}, output={"name": "Alice"}, schema=schema)
    assert not fail_res.passed


def test_tool_args_match_schema():
    tool_schema = {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"]
    }
    ok_res = run_assertion("tool_args_match_schema", case={}, output={"tool_calls": [{"name": "weather", "args": {"city": "Paris"}}]}, tool_schema=tool_schema)
    assert ok_res.passed
    fail_res = run_assertion("tool_args_match_schema", case={}, output={"tool_calls": [{"name": "weather", "args": {}}]}, tool_schema=tool_schema)
    assert not fail_res.passed


def test_contains_and_not_contains():
    assert run_assertion("contains", case={}, output="Hello world", substring="world").passed
    assert not run_assertion("contains", case={}, output="Hello world", substring="Mars").passed
    assert run_assertion("not_contains", case={}, output="Hello world", substring="Mars").passed
    assert not run_assertion("not_contains", case={}, output="Hello world", substring="world").passed


def test_regex():
    assert run_assertion("regex", case={}, output="Ref: 12345", pattern=r"\b\d{5}\b").passed
    assert not run_assertion("regex", case={}, output="Ref: abc", pattern=r"\b\d{5}\b").passed


def test_budgets():
    assert run_assertion("latency_under_budget", case={}, output={"latency_ms": 150}, budget_ms=200).passed
    assert not run_assertion("latency_under_budget", case={}, output={"latency_ms": 500}, budget_ms=200).passed
    assert run_assertion("cost_under_budget", case={}, output={"cost_usd": 0.01}, budget_usd=0.05).passed
    assert not run_assertion("cost_under_budget", case={}, output={"cost_usd": 0.10}, budget_usd=0.05).passed


def test_no_pii():
    assert run_assertion("no_pii", case={}, output="Contact support at portal.").passed
    assert not run_assertion("no_pii", case={}, output="Contact me at secret@example.com").passed
