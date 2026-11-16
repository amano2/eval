"""Assertion Registry and Result object."""

from typing import Any, Callable, Dict, Optional


class Result:
    def __init__(self, passed: bool, detail: str = "", skipped: bool = False):
        self.passed = passed
        self.detail = detail
        self.skipped = skipped

    @classmethod
    def ok(cls, detail: str = "Passed") -> "Result":
        return cls(passed=True, detail=detail)

    @classmethod
    def fail(cls, detail: str = "Failed") -> "Result":
        return cls(passed=False, detail=detail)

    @classmethod
    def skip(cls, detail: str = "Skipped") -> "Result":
        return cls(passed=True, detail=detail, skipped=True)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "detail": self.detail,
            "skipped": self.skipped
        }


_REGISTRY: Dict[str, Callable[..., Result]] = {}


def assertion(name: str):
    """Decorator to register an assertion."""
    def decorator(fn: Callable[..., Result]):
        _REGISTRY[name] = fn
        return fn
    return decorator


def get_assertion(name: str) -> Optional[Callable[..., Result]]:
    return _REGISTRY.get(name)


def list_assertions() -> Dict[str, Callable[..., Result]]:
    return dict(_REGISTRY)


def run_assertion(name: str, case: Any, output: Any, trace: Any = None, **kwargs) -> Result:
    fn = get_assertion(name)
    if not fn:
        return Result.fail(f"Assertion '{name}' is not registered")
    try:
        return fn(case, output, trace, **kwargs)
    except Exception as e:
        return Result.fail(f"Assertion error: {str(e)}")
