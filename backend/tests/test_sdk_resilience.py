"""Tests for SDK failure-tolerance and under-5ms overhead budget."""

import time
import pytest
from sdk.python.evalkit import init, trace, span, get_buffer


def test_sdk_server_down_failure_injection():
    # Configure SDK to point to a nonexistent port / server down
    init(endpoint="http://localhost:59999", api_key="test-key")

    # Host app execution MUST NOT raise any exceptions
    try:
        with trace(feature="payment", scenario="checkout", input_data={"amount": 100}) as t:
            with span("validate_card", kind="tool") as s:
                s.set_output({"valid": True})
            t.set_output({"status": "success"})
    except Exception as e:
        pytest.fail(f"Host app threw exception during server outage: {e}")

    # Force flush buffer
    buf = get_buffer()
    buf.flush()
    # Verified: failures were handled gracefully


def test_sdk_overhead_under_5ms():
    init(endpoint="http://localhost:59999", api_key="test-key")

    # Measure average overhead per span
    n_iterations = 100
    start = time.perf_counter()
    for i in range(n_iterations):
        with span(f"span_{i}", kind="model") as s:
            s.set_output("sample")
    total_duration = time.perf_counter() - start

    avg_overhead_ms = (total_duration / n_iterations) * 1000
    assert avg_overhead_ms < 5.0, f"Average span overhead {avg_overhead_ms:.2f}ms exceeded 5.0ms budget"
