"""Integration tests for Supabase Cloud Backend, Database & Payload Storage."""

import os
import pytest
from backend.app.db.supabase_client import get_supabase_client, is_supabase_enabled
from backend.app.core.storage import process_payload_for_storage, MAX_INLINE_PAYLOAD_BYTES


def test_supabase_configured():
    assert is_supabase_enabled(), "Supabase URL and Key must be configured in environment"
    sb = get_supabase_client()
    assert sb is not None


def test_supabase_read_write_trace():
    sb = get_supabase_client()
    assert sb is not None

    test_trace_id = "00000000-0000-0000-0000-000000009999"
    # Insert test trace
    insert_res = sb.table("traces").upsert({
        "id": test_trace_id,
        "project_id": "00000000-0000-0000-0000-000000000002",
        "feature": "integration_test",
        "scenario": "supabase_test",
        "input": {"test": "data"},
        "output": {"test": "result"}
    }).execute()
    assert len(insert_res.data) > 0

    # Query back
    query_res = sb.table("traces").select("*").eq("id", test_trace_id).execute()
    assert len(query_res.data) == 1
    assert query_res.data[0]["feature"] == "integration_test"


def test_large_payload_storage_offload():
    # Construct payload larger than 64 KB
    large_text = "A" * 70000
    large_payload = {"huge_content": large_text}

    preview, storage_key = process_payload_for_storage(large_payload, path_prefix="test/payloads")

    assert storage_key is not None
    assert storage_key.startswith("test/payloads/")
    assert preview["_truncated"] is True
    assert preview["_size_bytes"] > MAX_INLINE_PAYLOAD_BYTES
    assert len(preview["_preview"]) < 500


def test_small_payload_stays_inline():
    small_payload = {"small": "data"}
    preview, storage_key = process_payload_for_storage(small_payload, path_prefix="test/payloads")

    assert storage_key is None
    assert preview == small_payload
