"""Target Adapters: HTTP, CLI, Python Callable, and Replay."""

import json
import subprocess
import importlib
from typing import Any, Dict, Optional
import httpx


def call_http_target(url: str, case_input: Any, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(url, json=case_input, headers=req_headers)
        resp.raise_for_status()
        try:
            return resp.json()
        except Exception:
            return {"text": resp.text}


def call_cli_target(command: str, case_input: Any) -> Dict[str, Any]:
    proc = subprocess.run(
        command,
        input=json.dumps(case_input),
        capture_output=True,
        text=True,
        shell=True,
        timeout=30
    )
    if proc.returncode != 0:
        raise RuntimeError(f"CLI target error ({proc.returncode}): {proc.stderr}")
    try:
        return json.loads(proc.stdout)
    except Exception:
        return {"text": proc.stdout}


def call_python_target(target_ref: str, case_input: Any) -> Dict[str, Any]:
    """Call a python function formatted as 'module.path:function_name'."""
    mod_name, func_name = target_ref.split(":")
    mod = importlib.import_module(mod_name)
    func = getattr(mod, func_name)
    output = func(case_input)
    if isinstance(output, dict):
        return output
    return {"text": str(output)}


def call_replay_target(cache: Dict[str, Any], case_key: str) -> Dict[str, Any]:
    if case_key in cache:
        return cache[case_key]
    return {"text": f"Replayed output for {case_key}"}
