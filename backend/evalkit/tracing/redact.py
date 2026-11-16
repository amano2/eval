"""Redaction engine for secrets and PII."""

import re
from typing import Any, Dict, List, Union

# Common secret and PII regex patterns
PATTERNS = [
    (r"sk-[a-zA-Z0-9]{20,}", "[redacted: api_key]"),
    (r"ek_[a-zA-Z0-9_]{20,}", "[redacted: api_key]"),
    (r"ghp_[a-zA-Z0-9]{20,}", "[redacted: github_token]"),
    (r"Bearer\s+[a-zA-Z0-9_\-\.]{20,}", "Bearer [redacted: bearer_token]"),
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "[redacted: email]"),
    (r"\b\d{3}-\d{2}-\d{4}\b", "[redacted: ssn]"),
    (r"\b(?:\d{4}[-\s]?){3}\d{4}\b", "[redacted: credit_card]"),
    (r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b", "[redacted: ip_address]")
]

COMPILED_PATTERNS = [(re.compile(p, re.IGNORECASE), repl) for p, repl in PATTERNS]


def redact_text(text: str) -> str:
    """Apply all redaction rules to a string."""
    if not isinstance(text, str):
        return text
    result = text
    for pattern, repl in COMPILED_PATTERNS:
        result = pattern.sub(repl, result)
    return result


def redact_payload(obj: Any) -> Any:
    """Recursively redact dictionary, list, or primitive JSON payloads."""
    if isinstance(obj, str):
        return redact_text(obj)
    elif isinstance(obj, dict):
        return {k: redact_payload(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [redact_payload(item) for item in obj]
    return obj
