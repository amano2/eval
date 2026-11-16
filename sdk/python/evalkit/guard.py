"""EvalKit Runtime Guard — In-app validation with 1-shot retry."""

from typing import Any, Callable, Optional
from backend.evalkit.assertions.registry import run_assertion, Result


def guard(
    assertion_name: str,
    output: Any,
    case_input: Any = None,
    trace_data: Any = None,
    max_retries: int = 1,
    retry_callback: Optional[Callable[[str], Any]] = None,
    **kwargs
) -> Any:
    """Validate model output in real-time. If it fails and a retry callback is provided,
    retries once feeding the failure critique as feedback."""
    res = run_assertion(assertion_name, case=case_input, output=output, trace=trace_data, **kwargs)
    if res.passed:
        return output

    if max_retries > 0 and retry_callback is not None:
        # 1-shot retry with error detail as feedback
        corrected_output = retry_callback(f"Previous attempt violated rule '{assertion_name}': {res.detail}. Please correct it.")
        second_res = run_assertion(assertion_name, case=case_input, output=corrected_output, trace=trace_data, **kwargs)
        if second_res.passed:
            return corrected_output
        raise ValueError(f"Runtime Guard '{assertion_name}' failed after retry: {second_res.detail}")

    raise ValueError(f"Runtime Guard '{assertion_name}' violated: {res.detail}")
