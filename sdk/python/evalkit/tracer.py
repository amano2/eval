"""EvalKit Python SDK — Non-blocking, failure-tolerant trace and span collector."""

import os
import time
import queue
import logging
import threading
import contextvars
from typing import Any, Callable, Dict, List, Optional
from datetime import datetime, timezone
import requests
from backend.evalkit.tracing.redact import redact_payload

logger = logging.getLogger("evalkit.sdk")

# Context variables for trace & span hierarchy
_current_trace: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar("_current_trace", default=None)
_current_span: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar("_current_span", default=None)


class IngestBuffer:
    """Bounded in-memory queue flushed asynchronously by a background daemon thread."""

    def __init__(self, endpoint: str, api_key: str, max_queue: int = 10000, batch_size: int = 50, flush_interval: float = 1.0):
        self.endpoint = endpoint.rstrip("/") + "/api/ingest"
        self.api_key = api_key
        self.queue: queue.Queue = queue.Queue(maxsize=max_queue)
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.dropped_spans_count = 0
        self.failed_ingest_count = 0
        self._stop_event = threading.Event()
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    def enqueue(self, item: Dict[str, Any]):
        """Non-blocking enqueue. If queue is full, drops item and increments counter."""
        try:
            self.queue.put_nowait(item)
        except queue.Full:
            self.dropped_spans_count += 1
            logger.warning(f"EvalKit queue full. Dropped trace/span item (total dropped: {self.dropped_spans_count})")

    def _worker_loop(self):
        while not self._stop_event.is_set():
            batch = []
            start_time = time.time()
            while len(batch) < self.batch_size and (time.time() - start_time) < self.flush_interval:
                try:
                    item = self.queue.get(timeout=0.1)
                    batch.append(item)
                except queue.Empty:
                    break

            if batch:
                self._send_batch(batch)

    def _send_batch(self, batch: List[Dict[str, Any]]):
        if not self.endpoint or not self.api_key:
            return
        try:
            headers = {
                "X-API-Key": self.api_key,
                "Content-Type": "application/json"
            }
            resp = requests.post(self.endpoint, json={"batch": batch}, headers=headers, timeout=2.0)
            if resp.status_code >= 400:
                self.failed_ingest_count += len(batch)
        except Exception:
            # Absolute safety guarantee: never raise, drop and count
            self.failed_ingest_count += len(batch)

    def flush(self):
        """Flush remaining queue items synchronously (e.g. at shutdown)."""
        batch = []
        while not self.queue.empty():
            try:
                batch.append(self.queue.get_nowait())
            except queue.Empty:
                break
        if batch:
            self._send_batch(batch)


# Global SDK Client singleton
_global_buffer: Optional[IngestBuffer] = None

def init(endpoint: Optional[str] = None, api_key: Optional[str] = None):
    global _global_buffer
    ep = endpoint or os.getenv("EVALKIT_ENDPOINT", "http://localhost:8000")
    key = api_key or os.getenv("EVALKIT_API_KEY", "ek_live_dev_test_key_001")
    _global_buffer = IngestBuffer(endpoint=ep, api_key=key)


def get_buffer() -> IngestBuffer:
    global _global_buffer
    if _global_buffer is None:
        init()
    return _global_buffer  # type: ignore


class TraceContext:
    def __init__(
        self,
        feature: Optional[str] = None,
        scenario: Optional[str] = None,
        origin: str = "real",
        external_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        input_data: Any = None,
        model: Optional[str] = None,
        prompt_version: Optional[str] = None,
        app_version: Optional[str] = None
    ):
        self.trace_data = {
            "type": "trace",
            "feature": feature,
            "scenario": scenario,
            "origin": origin,
            "external_id": external_id,
            "tags": tags or [],
            "input": redact_payload(input_data),
            "output": None,
            "model": model,
            "prompt_version": prompt_version,
            "app_version": app_version,
            "status": "ok",
            "error": None,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cost_usd": 0.0,
            "latency_ms": None,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "spans": []
        }
        self.token = None
        self.start_t = 0.0

    def __enter__(self):
        self.start_t = time.perf_counter()
        self.token = _current_trace.set(self.trace_data)
        return self

    def set_output(self, output: Any):
        self.trace_data["output"] = redact_payload(output)

    def set_error(self, err: Exception):
        self.trace_data["status"] = "error"
        self.trace_data["error"] = str(err)

    def add_tokens(self, prompt: int = 0, completion: int = 0, cost: float = 0.0):
        self.trace_data["prompt_tokens"] += prompt
        self.trace_data["completion_tokens"] += completion
        self.trace_data["cost_usd"] += cost

    def __exit__(self, exc_type, exc_val, exc_tb):
        latency = int((time.perf_counter() - self.start_t) * 1000)
        self.trace_data["latency_ms"] = latency
        if exc_val:
            self.set_error(exc_val)
        _current_trace.reset(self.token)
        get_buffer().enqueue(self.trace_data)
        return False  # Don't suppress application exceptions


class SpanContext:
    def __init__(
        self,
        name: str,
        kind: str = "other",  # model, retrieval, tool, guardrail, assertion, judge, other
        attributes: Optional[Dict[str, Any]] = None,
        input_data: Any = None
    ):
        self.span_data = {
            "type": "span",
            "name": name,
            "kind": kind,
            "attributes": attributes or {},
            "input": redact_payload(input_data),
            "output": None,
            "status": "ok",
            "error": None,
            "started_at": datetime.now(timezone.utc).isoformat()
        }
        self.token = None
        self.start_t = 0.0

    def __enter__(self):
        self.start_t = time.perf_counter()
        self.token = _current_span.set(self.span_data)
        return self

    def set_output(self, output: Any):
        self.span_data["output"] = redact_payload(output)

    def set_error(self, err: Exception):
        self.span_data["status"] = "error"
        self.span_data["error"] = str(err)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_val:
            self.set_error(exc_val)
        parent_trace = _current_trace.get()
        if parent_trace is not None:
            parent_trace["spans"].append(self.span_data)
        _current_span.reset(self.token)
        return False


def trace(feature: Optional[str] = None, scenario: Optional[str] = None, **kwargs) -> TraceContext:
    return TraceContext(feature=feature, scenario=scenario, **kwargs)

def span(name: str, kind: str = "other", **kwargs) -> SpanContext:
    return SpanContext(name=name, kind=kind, **kwargs)

def traced(feature: Optional[str] = None, scenario: Optional[str] = None):
    """Decorator to trace a function call automatically."""
    def decorator(fn: Callable):
        def wrapper(*args, **kwargs):
            feat = feature or fn.__name__
            with trace(feature=feat, scenario=scenario, input_data={"args": args, "kwargs": kwargs}) as t:
                result = fn(*args, **kwargs)
                t.set_output(result)
                return result
        return wrapper
    return decorator
