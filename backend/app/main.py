"""FastAPI Application for EvalKit."""

import os
import hashlib
import json
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from fastapi import FastAPI, Depends, HTTPException, Header, status, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from backend.app.db.session import init_db, get_db, get_db_context
from backend.app.db.supabase_client import get_supabase_client, is_supabase_enabled
from backend.app.db.models import (
    ApiKey, Project, Trace, Span, HumanLabel, Dataset, Case, EvalRun,
    EvalResult, Judge, JudgeVerdict, Gate, GateResult, FailureTag
)
from backend.evalkit.tracing.redact import redact_payload
from backend.app.core.storage import process_payload_for_storage
from backend.evalkit.runner.run import execute_run
from backend.evalkit.runner.gate import evaluate_gates_for_run
from backend.evalkit.judges.alignment import compute_judge_alignment
from backend.evalkit.datasets.manager import generate_synthetic_cases, convert_trace_to_case
from backend.evalkit.export.finetune import export_finetune_data

app = FastAPI(title="EvalKit", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db()


def verify_api_key(x_api_key: Optional[str] = Header(None), db: Session = Depends(get_db)) -> ApiKey:
    """Validate incoming API key from header."""
    if not x_api_key:
        # Check dev key fallback
        key_hash = hashlib.sha256("ek_live_dev_test_key_001".encode()).hexdigest()
        key_rec = db.query(ApiKey).filter(ApiKey.key_hash == key_hash).first()
        if key_rec:
            return key_rec
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing X-API-Key header")

    key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    key_rec = db.query(ApiKey).filter(ApiKey.key_hash == key_hash).first()
    if not key_rec:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")
    return key_rec


# --- INGESTION ---
@app.post("/api/ingest")
async def ingest_traces(payload: Dict[str, Any], api_key: ApiKey = Depends(verify_api_key), db: Session = Depends(get_db)):
    batch = payload.get("batch", [])
    if not batch and "type" in payload:
        batch = [payload]

    ingested_count = 0
    for item in batch:
        if item.get("type") == "trace":
            ext_id = item.get("external_id")
            # Idempotent ingest check
            trace = None
            if ext_id:
                trace = db.query(Trace).filter(
                    Trace.project_id == api_key.project_id,
                    Trace.external_id == ext_id
                ).first()

            trace_inp, _ = process_payload_for_storage(redact_payload(item.get("input")), path_prefix="traces/input")
            trace_out, _ = process_payload_for_storage(redact_payload(item.get("output")), path_prefix="traces/output")

            if not trace:
                trace = Trace(
                    project_id=api_key.project_id,
                    external_id=ext_id,
                    feature=item.get("feature"),
                    scenario=item.get("scenario"),
                    origin=item.get("origin", "real"),
                    tags=item.get("tags", []),
                    input=trace_inp,
                    output=trace_out,
                    model=item.get("model"),
                    prompt_version=item.get("prompt_version"),
                    app_version=item.get("app_version"),
                    status=item.get("status", "ok"),
                    error=item.get("error"),
                    prompt_tokens=item.get("prompt_tokens", 0),
                    completion_tokens=item.get("completion_tokens", 0),
                    cost_usd=item.get("cost_usd", 0.0),
                    latency_ms=item.get("latency_ms"),
                    eval_run_id=item.get("eval_run_id")
                )
                db.add(trace)
                db.flush()
            else:
                trace.output = trace_out
                trace.status = item.get("status", trace.status)

            # Ingest spans
            spans_data = item.get("spans", [])
            for s in spans_data:
                s_inp, s_inp_k = process_payload_for_storage(redact_payload(s.get("input")), path_prefix="spans/input")
                s_out, s_out_k = process_payload_for_storage(redact_payload(s.get("output")), path_prefix="spans/output")
                span = Span(
                    trace_id=trace.id,
                    kind=s.get("kind", "other"),
                    name=s.get("name", "span"),
                    input=s_inp,
                    output=s_out,
                    input_key=s_inp_k,
                    output_key=s_out_k,
                    attributes=s.get("attributes", {}),
                    status=s.get("status", "ok"),
                    error=s.get("error")
                )
                db.add(span)
            ingested_count += 1

            # Sync to Supabase cloud if enabled
            if is_supabase_enabled():
                try:
                    sb = get_supabase_client()
                    if sb:
                        sb.table("traces").insert({
                            "id": trace.id,
                            "project_id": "00000000-0000-0000-0000-000000000002",
                            "external_id": trace.external_id,
                            "feature": trace.feature,
                            "scenario": trace.scenario,
                            "origin": trace.origin,
                            "tags": trace.tags,
                            "input": trace.input,
                            "output": trace.output,
                            "model": trace.model,
                            "prompt_version": trace.prompt_version,
                            "app_version": trace.app_version,
                            "status": trace.status,
                            "error": trace.error,
                            "latency_ms": trace.latency_ms,
                            "cost_usd": float(trace.cost_usd or 0.0)
                        }).execute()
                except Exception as e:
                    pass

    db.commit()
    return {"status": "ok", "ingested": ingested_count}


# --- TRACES & REVIEW ---
@app.get("/api/traces")
def get_traces(
    queue: str = "unlabelled",  # unlabelled, failed_assertions, judge_disagrees, low_rating, all
    feature: Optional[str] = None,
    scenario: Optional[str] = None,
    db: Session = Depends(get_db)
):
    query = db.query(Trace)
    if feature:
        query = query.filter(Trace.feature == feature)
    if scenario:
        query = query.filter(Trace.scenario == scenario)

    if queue == "unlabelled":
        query = query.filter(~Trace.labels.any())
    elif queue == "failed_assertions":
        query = query.filter(Trace.spans.any(Span.kind == "assertion", Span.status == "error"))
    elif queue == "low_rating":
        query = query.filter(Trace.user_rating == -1)

    traces = query.order_by(Trace.started_at.desc()).limit(100).all()
    results = []
    for t in traces:
        results.append({
            "id": t.id,
            "feature": t.feature,
            "scenario": t.scenario,
            "origin": t.origin,
            "tags": t.tags,
            "status": t.status,
            "latency_ms": t.latency_ms,
            "started_at": t.started_at.isoformat() if t.started_at else None,
            "is_labelled": len(t.labels) > 0,
            "input": t.input,
            "output": t.output
        })
    return {"traces": results, "total": len(results)}


@app.get("/api/traces/{trace_id}")
def get_trace_detail(trace_id: str, db: Session = Depends(get_db)):
    t = db.query(Trace).filter(Trace.id == trace_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Trace not found")

    spans = [
        {
            "id": s.id,
            "kind": s.kind,
            "name": s.name,
            "status": s.status,
            "attributes": s.attributes,
            "input": s.input,
            "output": s.output
        }
        for s in t.spans
    ]
    labels = [
        {
            "id": l.id,
            "label": l.label,
            "critique": l.critique,
            "failure_tags": l.failure_tags,
            "ideal_output": l.ideal_output
        }
        for l in t.labels
    ]
    verdicts = [
        {
            "id": v.id,
            "verdict": v.verdict,
            "critique": v.critique
        }
        for v in t.verdicts
    ]

    return {
        "id": t.id,
        "feature": t.feature,
        "scenario": t.scenario,
        "origin": t.origin,
        "tags": t.tags,
        "input": t.input,
        "output": t.output,
        "model": t.model,
        "prompt_version": t.prompt_version,
        "app_version": t.app_version,
        "latency_ms": t.latency_ms,
        "cost_usd": float(t.cost_usd or 0.0),
        "status": t.status,
        "error": t.error,
        "spans": spans,
        "labels": labels,
        "verdicts": verdicts
    }


@app.post("/api/traces/{trace_id}/labels")
def add_human_label(trace_id: str, payload: Dict[str, Any], db: Session = Depends(get_db)):
    t = db.query(Trace).filter(Trace.id == trace_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Trace not found")

    label_val = payload.get("label", "good")
    critique = payload.get("critique", "")
    ideal = payload.get("ideal_output")
    failure_tags = payload.get("failure_tags", [])
    blind = payload.get("blind", False)

    hl = HumanLabel(
        project_id=t.project_id,
        trace_id=t.id,
        labeler_id="usr-admin-001",
        criterion="overall",
        label=label_val,
        critique=critique,
        ideal_output=ideal,
        failure_tags=failure_tags,
        blind=blind,
        seconds_spent=payload.get("seconds_spent", 5)
    )
    db.add(hl)
    db.commit()

    if is_supabase_enabled():
        try:
            sb = get_supabase_client()
            if sb:
                sb.table("human_labels").insert({
                    "id": hl.id,
                    "project_id": "00000000-0000-0000-0000-000000000002",
                    "trace_id": hl.trace_id,
                    "labeler_id": "00000000-0000-0000-0000-000000000001",
                    "criterion": hl.criterion,
                    "label": hl.label,
                    "critique": hl.critique,
                    "ideal_output": hl.ideal_output,
                    "failure_tags": hl.failure_tags,
                    "blind": hl.blind,
                    "seconds_spent": hl.seconds_spent
                }).execute()
        except Exception:
            pass
    return {"status": "ok", "label_id": hl.id}


@app.post("/api/traces/{trace_id}/add_to_dataset")
def add_trace_to_dataset_endpoint(trace_id: str, payload: Dict[str, Any]):
    dataset_id = payload.get("dataset_id")
    if not dataset_id:
        raise HTTPException(status_code=400, detail="dataset_id is required")
    case = convert_trace_to_case(trace_id, dataset_id, scenario=payload.get("scenario"))
    return {"status": "ok", "case_id": case.id}


# --- DATASETS ---
@app.get("/api/datasets")
def list_datasets(db: Session = Depends(get_db)):
    datasets = db.query(Dataset).all()
    return [
        {
            "id": d.id,
            "name": d.name,
            "feature": d.feature,
            "description": d.description,
            "is_golden": d.is_golden,
            "holdout": d.holdout,
            "cases_count": len(d.cases)
        }
        for d in datasets
    ]


@app.post("/api/datasets/{dataset_id}/generate")
def generate_cases_api(dataset_id: str, payload: Dict[str, Any]):
    feature = payload.get("feature", "qa")
    scenario = payload.get("scenario", "answer_in_docs")
    persona = payload.get("persona", "engineer")
    count = int(payload.get("count", 5))
    cases = generate_synthetic_cases(dataset_id, feature, scenario, persona, count)
    return {"status": "ok", "generated_count": len(cases), "cases": cases}


# --- EVAL RUNS & GATES ---
@app.post("/api/runs")
def trigger_eval_run(payload: Dict[str, Any]):
    res = execute_run(
        project_id=payload.get("project_id", "prj-default-001"),
        dataset_id=payload.get("dataset_id"),
        level=payload.get("level", 1),
        executor=payload.get("executor", "fake"),
        baseline_run_id=payload.get("baseline_run_id"),
        config=payload.get("config", {})
    )
    return res


@app.get("/api/runs/{run_id}")
def get_run_details(run_id: str, db: Session = Depends(get_db)):
    run = db.query(EvalRun).filter(EvalRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return {
        "id": run.id,
        "dataset_id": run.dataset_id,
        "level": run.level,
        "executor": run.executor,
        "status": run.status,
        "summary": run.summary,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None
    }


@app.get("/api/runs/{candidate_id}/compare/{baseline_id}")
def compare_runs_api(candidate_id: str, baseline_id: str, db: Session = Depends(get_db)):
    c_run = db.query(EvalRun).filter(EvalRun.id == candidate_id).first()
    b_run = db.query(EvalRun).filter(EvalRun.id == baseline_id).first()
    if not c_run or not b_run:
        raise HTTPException(status_code=404, detail="Runs not found")

    c_summary = c_run.summary or {}
    b_summary = b_run.summary or {}

    c_scens = c_summary.get("scenarios", {})
    b_scens = b_summary.get("scenarios", {})

    comparison = []
    all_scens = set(c_scens.keys()).union(b_scens.keys())
    for scen in all_scens:
        b_rate = b_scens.get(scen, {}).get("pass_rate", 0.0)
        c_rate = c_scens.get(scen, {}).get("pass_rate", 0.0)
        delta = round(c_rate - b_rate, 4)
        status_label = "no_change"
        if delta < -0.05:
            status_label = "regression"
        elif delta > 0.05:
            status_label = "improvement"
        comparison.append({
            "scenario": scen,
            "baseline_pass_rate": b_rate,
            "candidate_pass_rate": c_rate,
            "delta": delta,
            "status": status_label,
            "significance": "significant" if c_scens.get(scen, {}).get("total", 0) >= 30 else "not enough data"
        })

    return {"candidate_id": candidate_id, "baseline_id": baseline_id, "comparison": comparison}


@app.post("/api/gates/check")
def check_gate_api(payload: Dict[str, Any]):
    run_id = payload.get("run_id")
    if not run_id:
        raise HTTPException(status_code=400, detail="run_id required")
    passed, reports = evaluate_gates_for_run(run_id)
    return {"passed": passed, "gates": reports}


# --- JUDGES & ALIGNMENT ---
@app.get("/api/judges/{judge_id}/alignment")
def judge_alignment_api(judge_id: str):
    return compute_judge_alignment(judge_id)


# --- EXPORT ---
@app.post("/api/export/finetune")
def export_finetune_api(payload: Dict[str, Any]):
    project_id = payload.get("project_id", "prj-default-001")
    path = payload.get("output_path", "./exports/finetune.jsonl")
    return export_finetune_data(project_id, path)


# --- INTERACTIVE WEB CONSOLE & PRESENTATION LANDING PAGE ---
@app.get("/", response_class=HTMLResponse)
def serve_landing_page():
    landing_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "frontend", "index.html")
    if os.path.exists(landing_path):
        with open(landing_path, "r", encoding="utf-8") as f:
            return f.read()
    return serve_console()


@app.get("/console", response_class=HTMLResponse)
def serve_console():
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>EvalKit Console</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    .kbd-key { background: #374151; color: #f3f4f6; border-radius: 4px; padding: 2px 6px; font-size: 0.75rem; font-family: monospace; }
  </style>
</head>
<body class="bg-gray-900 text-gray-100 flex h-screen overflow-hidden">
  <!-- Sidebar Navigation -->
  <aside class="w-64 bg-gray-950 border-r border-gray-800 flex flex-col justify-between">
    <div>
      <div class="p-4 border-b border-gray-800 flex items-center justify-between">
        <div class="flex items-center space-x-2">
          <i class="fa-solid fa-chart-line text-blue-500 text-xl"></i>
          <span class="font-bold text-lg tracking-wide">EvalKit</span>
          <span class="bg-blue-900 text-blue-300 text-xs px-2 py-0.5 rounded font-mono">v0.1</span>
        </div>
        <a href="/" class="text-xs text-blue-400 hover:text-blue-300 flex items-center space-x-1" title="Back to Project Landing Page">
          <i class="fa-solid fa-home"></i>
        </a>
      </div>
      <nav class="p-4 space-y-1">
        <button onclick="switchTab('traces')" id="nav-traces" class="w-full text-left px-3 py-2 rounded flex items-center space-x-3 bg-gray-800 text-white font-medium">
          <i class="fa-solid fa-list-check w-5 text-gray-400"></i><span>Traces & Review</span>
        </button>
        <button onclick="switchTab('datasets')" id="nav-datasets" class="w-full text-left px-3 py-2 rounded flex items-center space-x-3 text-gray-400 hover:bg-gray-800 hover:text-white">
          <i class="fa-solid fa-database w-5 text-gray-400"></i><span>Datasets</span>
        </button>
        <button onclick="switchTab('runs')" id="nav-runs" class="w-full text-left px-3 py-2 rounded flex items-center space-x-3 text-gray-400 hover:bg-gray-800 hover:text-white">
          <i class="fa-solid fa-play w-5 text-gray-400"></i><span>Runs & Compare</span>
        </button>
        <button onclick="switchTab('judges')" id="nav-judges" class="w-full text-left px-3 py-2 rounded flex items-center space-x-3 text-gray-400 hover:bg-gray-800 hover:text-white">
          <i class="fa-solid fa-scale-balanced w-5 text-gray-400"></i><span>Judge Alignment</span>
        </button>
        <button onclick="switchTab('gates')" id="nav-gates" class="w-full text-left px-3 py-2 rounded flex items-center space-x-3 text-gray-400 hover:bg-gray-800 hover:text-white">
          <i class="fa-solid fa-shield-halved w-5 text-gray-400"></i><span>Release Gates</span>
        </button>
      </nav>
    </div>
    <div class="p-4 border-t border-gray-800 text-xs text-gray-500 flex justify-between items-center">
      <span>Shortcuts: <span class="kbd-key">?</span></span>
      <span class="text-green-400 font-mono"><i class="fa-solid fa-circle text-xs mr-1"></i>Connected</span>
    </div>
  </aside>

  <!-- Main Content Area -->
  <main class="flex-1 flex flex-col overflow-hidden bg-gray-900">
    <!-- Topbar -->
    <header class="h-14 border-b border-gray-800 flex items-center justify-between px-6 bg-gray-950">
      <div class="flex items-center space-x-4">
        <h1 id="page-title" class="text-base font-semibold">Traces Review Queue</h1>
        <div id="blind-toggle" class="flex items-center space-x-2 text-xs text-gray-400 bg-gray-800 px-3 py-1 rounded">
          <input type="checkbox" id="blind-checkbox" onchange="toggleBlindReview()" class="rounded bg-gray-700">
          <label for="blind-checkbox">Blind Review (Hide Judge)</label>
        </div>
      </div>
      <div id="toast-container" class="hidden bg-blue-600 text-white text-xs px-3 py-1.5 rounded shadow">
        <span id="toast-msg">Label saved.</span>
        <button onclick="undoLabel()" class="ml-2 underline font-bold">Undo</button>
      </div>
    </header>

    <!-- Workspace Tabs -->
    <div class="flex-1 flex overflow-hidden">
      <!-- TRACES VIEW -->
      <section id="tab-traces" class="flex-1 flex overflow-hidden">
        <!-- Trace Queue Column -->
        <div class="w-80 border-r border-gray-800 flex flex-col bg-gray-950">
          <div class="p-3 border-b border-gray-800">
            <select id="queue-select" onchange="loadTraces()" class="w-full bg-gray-800 text-xs text-gray-200 rounded p-2 border border-gray-700 focus:outline-none">
              <option value="unlabelled">Queue: Unlabelled Traces</option>
              <option value="failed_assertions">Queue: Failed Assertions</option>
              <option value="low_rating">Queue: Low Rating</option>
              <option value="all">Queue: All Traces</option>
            </select>
          </div>
          <div id="trace-list" class="flex-1 overflow-y-auto divide-y divide-gray-800 text-xs">
            <div class="p-4 text-gray-500 text-center">Loading traces...</div>
          </div>
        </div>

        <!-- Single Screen Review Column -->
        <div class="flex-1 flex flex-col overflow-y-auto p-6 space-y-4">
          <div id="trace-detail-container" class="space-y-4">
            <div class="bg-gray-800 p-8 rounded text-center text-gray-400">
              Select a trace from the left queue or press <span class="kbd-key">J</span> / <span class="kbd-key">K</span> to navigate.
            </div>
          </div>
        </div>
      </section>

      <!-- DATASETS VIEW -->
      <section id="tab-datasets" class="hidden flex-1 p-6 overflow-y-auto space-y-6">
        <div class="flex justify-between items-center">
          <h2 class="text-xl font-bold">Datasets & Cases</h2>
          <button onclick="promptSyntheticGeneration()" class="bg-blue-600 hover:bg-blue-500 text-white text-xs px-4 py-2 rounded flex items-center space-x-2">
            <i class="fa-solid fa-wand-magic-sparkles"></i><span>Generate Synthetic Cases</span>
          </button>
        </div>
        <div id="datasets-table" class="bg-gray-950 border border-gray-800 rounded overflow-hidden text-sm">
          <div class="p-4 text-gray-400 text-center">Loading datasets...</div>
        </div>
      </section>

      <!-- RUNS & COMPARE VIEW -->
      <section id="tab-runs" class="hidden flex-1 p-6 overflow-y-auto space-y-6">
        <div class="flex justify-between items-center">
          <h2 class="text-xl font-bold">Eval Runs & Regression Comparison</h2>
          <button onclick="triggerQuickRun()" class="bg-green-600 hover:bg-green-500 text-white text-xs px-4 py-2 rounded flex items-center space-x-2">
            <i class="fa-solid fa-play"></i><span>Trigger Level-1 Run</span>
          </button>
        </div>
        <div id="runs-content" class="space-y-4">
          <div class="p-4 bg-gray-950 border border-gray-800 rounded text-sm text-gray-400">
            Click "Trigger Level-1 Run" to run Level-1 suite, or select runs to compare deltas.
          </div>
        </div>
      </section>

      <!-- JUDGE ALIGNMENT VIEW -->
      <section id="tab-judges" class="hidden flex-1 p-6 overflow-y-auto space-y-6">
        <h2 class="text-xl font-bold">Judge Alignment (Precision & Recall)</h2>
        <div id="alignment-dashboard" class="space-y-6 text-sm">
          <div class="p-4 text-gray-400 text-center bg-gray-950 rounded">Loading alignment metrics...</div>
        </div>
      </section>

      <!-- GATES VIEW -->
      <section id="tab-gates" class="hidden flex-1 p-6 overflow-y-auto space-y-6">
        <h2 class="text-xl font-bold">Release Gates Policy</h2>
        <div id="gates-dashboard" class="space-y-4 text-sm">
          <div class="p-4 text-gray-400 text-center bg-gray-950 rounded">Loading release gates status...</div>
        </div>
      </section>
    </div>
  </main>

  <script>
    let activeTraces = [];
    let currentTraceIdx = -1;
    let blindReview = false;
    let lastLabelAction = null;

    function switchTab(tab) {
      ['traces', 'datasets', 'runs', 'judges', 'gates'].forEach(t => {
        document.getElementById('tab-' + t).classList.add('hidden');
        document.getElementById('nav-' + t).className = "w-full text-left px-3 py-2 rounded flex items-center space-x-3 text-gray-400 hover:bg-gray-800 hover:text-white";
      });
      document.getElementById('tab-' + tab).classList.remove('hidden');
      document.getElementById('nav-' + tab).className = "w-full text-left px-3 py-2 rounded flex items-center space-x-3 bg-gray-800 text-white font-medium";

      if (tab === 'traces') loadTraces();
      if (tab === 'datasets') loadDatasets();
      if (tab === 'judges') loadAlignment();
      if (tab === 'gates') loadGates();
    }

    async function loadTraces() {
      const q = document.getElementById('queue-select').value;
      const res = await fetch(`/api/traces?queue=${q}`);
      const data = await res.json();
      activeTraces = data.traces || [];
      const listEl = document.getElementById('trace-list');
      if (activeTraces.length === 0) {
        listEl.innerHTML = '<div class="p-4 text-gray-500 text-center">Queue is empty. All caught up!</div>';
        document.getElementById('trace-detail-container').innerHTML = '<div class="p-8 text-center text-gray-500">Queue is empty.</div>';
        return;
      }
      listEl.innerHTML = activeTraces.map((t, idx) => `
        <div onclick="selectTrace(${idx})" class="p-3 cursor-pointer hover:bg-gray-900 flex justify-between items-center ${idx === currentTraceIdx ? 'bg-gray-900 border-l-2 border-blue-500' : ''}">
          <div class="truncate pr-2">
            <div class="font-mono text-gray-300 truncate">${t.id.substring(0,8)}... <span class="text-gray-500">(${t.feature || 'trace'})</span></div>
            <div class="text-gray-400 truncate">${t.input ? JSON.stringify(t.input).substring(0, 32) : 'No input'}</div>
          </div>
          <div>
            ${t.is_labelled ? '<i class="fa-solid fa-circle-check text-green-500 text-xs"></i>' : '<i class="fa-solid fa-circle-dot text-yellow-500 text-xs"></i>'}
          </div>
        </div>
      `).join('');
      if (currentTraceIdx < 0 && activeTraces.length > 0) {
        selectTrace(0);
      }
    }

    async function selectTrace(idx) {
      if (idx < 0 || idx >= activeTraces.length) return;
      currentTraceIdx = idx;
      loadTraces();
      const t = activeTraces[idx];
      const res = await fetch(`/api/traces/${t.id}`);
      const detail = await res.json();

      const container = document.getElementById('trace-detail-container');
      container.innerHTML = `
        <div class="bg-gray-950 border border-gray-800 rounded p-4 space-y-4">
          <div class="flex justify-between items-center border-b border-gray-800 pb-2">
            <div>
              <span class="font-mono text-sm text-blue-400 font-bold">${detail.id}</span>
              <span class="text-xs text-gray-500 ml-2">Feature: ${detail.feature || 'None'} / Scenario: ${detail.scenario || 'None'}</span>
            </div>
            <div class="text-xs text-gray-400 space-x-3">
              <span>Latency: ${detail.latency_ms || 0}ms</span>
              <span>Cost: $${(detail.cost_usd || 0).toFixed(4)}</span>
            </div>
          </div>

          <div>
            <div class="text-xs font-semibold text-gray-400 mb-1">INPUT</div>
            <div class="bg-gray-900 p-3 rounded font-mono text-xs text-gray-200 border border-gray-800">${JSON.stringify(detail.input, null, 2)}</div>
          </div>

          <div>
            <div class="text-xs font-semibold text-gray-400 mb-1">OUTPUT</div>
            <div class="bg-gray-900 p-3 rounded font-mono text-xs text-gray-200 border border-gray-800">${JSON.stringify(detail.output, null, 2)}</div>
          </div>

          <div id="judge-section" class="${blindReview ? 'hidden' : ''}">
            <div class="text-xs font-semibold text-gray-400 mb-1">LLM JUDGE VERDICT</div>
            <div class="bg-gray-900 p-3 rounded text-xs text-gray-300 border border-gray-800">
              ${detail.verdicts.length > 0 ? `
                <span class="px-2 py-0.5 rounded font-bold ${detail.verdicts[0].verdict === 'good' ? 'bg-green-900 text-green-300' : 'bg-red-900 text-red-300'}">${detail.verdicts[0].verdict.toUpperCase()}</span>
                <span class="ml-2">${detail.verdicts[0].critique || ''}</span>
              ` : '<span class="text-gray-500">No judge verdict yet</span>'}
            </div>
          </div>

          <!-- Review Actions -->
          <div class="border-t border-gray-800 pt-4 flex flex-col space-y-3">
            <div class="flex items-center space-x-3">
              <button onclick="submitLabel('good')" class="bg-green-600 hover:bg-green-500 text-white text-xs px-4 py-2 rounded font-bold flex items-center space-x-2">
                <span>Good</span> <span class="kbd-key">G</span>
              </button>
              <button onclick="submitLabel('bad')" class="bg-red-600 hover:bg-red-500 text-white text-xs px-4 py-2 rounded font-bold flex items-center space-x-2">
                <span>Bad</span> <span class="kbd-key">B</span>
              </button>
              <input type="text" id="critique-input" placeholder="One-line critique (Press C to focus)" class="flex-1 bg-gray-900 border border-gray-800 rounded p-2 text-xs text-gray-200 focus:outline-none focus:border-blue-500">
            </div>
            <div class="flex justify-between items-center text-xs text-gray-500">
              <span>Failure Tags: [wrong_citation] [hallucination] [prompt_leak]</span>
              <button onclick="promptAddToDataset('${detail.id}')" class="text-blue-400 hover:underline"><i class="fa-solid fa-plus mr-1"></i>Add to Dataset <span class="kbd-key">D</span></button>
            </div>
          </div>
        </div>
      `;
    }

    async function submitLabel(labelVal) {
      if (currentTraceIdx < 0 || currentTraceIdx >= activeTraces.length) return;
      const t = activeTraces[currentTraceIdx];
      const critique = document.getElementById('critique-input').value;
      const res = await fetch(`/api/traces/${t.id}/labels`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ label: labelVal, critique, blind: blindReview })
      });
      lastLabelAction = { traceId: t.id, label: labelVal };
      showToast(`Marked ${labelVal.toUpperCase()}`);
      // Advance to next trace
      if (currentTraceIdx + 1 < activeTraces.length) {
        selectTrace(currentTraceIdx + 1);
      } else {
        loadTraces();
      }
    }

    function showToast(msg) {
      const el = document.getElementById('toast-container');
      document.getElementById('toast-msg').innerText = msg;
      el.classList.remove('hidden');
      setTimeout(() => el.classList.add('hidden'), 5000);
    }

    function toggleBlindReview() {
      blindReview = document.getElementById('blind-checkbox').checked;
      const jSection = document.getElementById('judge-section');
      if (jSection) {
        if (blindReview) jSection.classList.add('hidden');
        else jSection.classList.remove('hidden');
      }
    }

    // Keyboard Shortcuts
    window.addEventListener('keydown', (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
      if (e.key === 'g' || e.key === 'G') submitLabel('good');
      if (e.key === 'b' || e.key === 'B') submitLabel('bad');
      if (e.key === 'j' || e.key === 'J' || e.key === 'ArrowDown') selectTrace(currentTraceIdx + 1);
      if (e.key === 'k' || e.key === 'K' || e.key === 'ArrowUp') selectTrace(currentTraceIdx - 1);
      if (e.key === 'c' || e.key === 'C') {
        e.preventDefault();
        const inp = document.getElementById('critique-input');
        if (inp) inp.focus();
      }
    });

    async function loadDatasets() {
      const res = await fetch('/api/datasets');
      const data = await res.json();
      const el = document.getElementById('datasets-table');
      el.innerHTML = `
        <table class="w-full text-left">
          <thead class="bg-gray-900 border-b border-gray-800 text-xs text-gray-400">
            <tr><th class="p-3">Name</th><th class="p-3">Feature</th><th class="p-3">Cases</th><th class="p-3">Golden Set</th></tr>
          </thead>
          <tbody class="divide-y divide-gray-800 text-xs">
            ${data.map(d => `
              <tr>
                <td class="p-3 font-mono font-bold text-blue-400">${d.name}</td>
                <td class="p-3">${d.feature}</td>
                <td class="p-3">${d.cases_count}</td>
                <td class="p-3">${d.is_golden ? '<span class="text-yellow-400 font-bold">Golden Set</span>' : '<span class="text-gray-500">Standard</span>'}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      `;
    }

    async function triggerQuickRun() {
      alert("Triggering Level-1 Run across golden dataset...");
      const res = await fetch('/api/runs', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ dataset_id: 'ds-golden-001', level: 1, executor: 'fake' })
      });
      const data = await res.json();
      document.getElementById('runs-content').innerHTML = `
        <div class="bg-gray-950 border border-gray-800 p-4 rounded text-xs space-y-3 font-mono">
          <div class="text-green-400 font-bold">Run Finished: ${data.run_id}</div>
          <div>Overall Pass Rate: ${(data.summary.pass_rate * 100).toFixed(1)}% (${data.summary.passed_cases}/${data.summary.total_cases})</div>
          <div>Average Latency: ${data.summary.avg_latency_ms}ms | Cost: $${data.summary.total_cost_usd}</div>
          <pre class="bg-gray-900 p-2 rounded">${JSON.stringify(data.summary.scenarios, null, 2)}</pre>
        </div>
      `;
    }

    async function loadAlignment() {
      const res = await fetch('/api/judges/judge-align-test/alignment');
      const data = await res.json();
      const el = document.getElementById('alignment-dashboard');
      const cm = data.confusion_matrix || {tp:0, fp:0, fn:0, tn:0};
      el.innerHTML = `
        <div class="grid grid-cols-2 gap-4">
          <div class="bg-gray-950 border border-gray-800 p-4 rounded">
            <h3 class="text-xs font-bold text-gray-400 mb-2">BAD CLASS (Failures - Positive Class)</h3>
            <div class="text-2xl font-bold text-red-400">Precision: ${data.metrics.bad_class.precision}</div>
            <div class="text-2xl font-bold text-yellow-400">Recall: ${data.metrics.bad_class.recall}</div>
          </div>
          <div class="bg-gray-950 border border-gray-800 p-4 rounded">
            <h3 class="text-xs font-bold text-gray-400 mb-2">GOOD CLASS</h3>
            <div class="text-2xl font-bold text-green-400">Precision: ${data.metrics.good_class.precision}</div>
            <div class="text-2xl font-bold text-blue-400">Recall: ${data.metrics.good_class.recall}</div>
          </div>
        </div>

        <div class="bg-gray-950 border border-gray-800 p-4 rounded">
          <h3 class="text-xs font-bold text-gray-400 mb-3">CONFUSION MATRIX (Total Labeled: ${data.total_labeled})</h3>
          <div class="grid grid-cols-2 gap-2 text-center font-mono text-sm max-w-sm">
            <div class="bg-gray-900 p-3 rounded"><div class="text-gray-500 text-xs">TP (True Bad)</div><div class="text-green-400 font-bold">${cm.tp}</div></div>
            <div class="bg-gray-900 p-3 rounded"><div class="text-gray-500 text-xs">FP (False Bad)</div><div class="text-red-400 font-bold">${cm.fp}</div></div>
            <div class="bg-gray-900 p-3 rounded"><div class="text-gray-500 text-xs">FN (Missed Bad)</div><div class="text-red-400 font-bold">${cm.fn}</div></div>
            <div class="bg-gray-900 p-3 rounded"><div class="text-gray-500 text-xs">TN (True Good)</div><div class="text-green-400 font-bold">${cm.tn}</div></div>
          </div>
        </div>

        <div class="bg-gray-950 border border-gray-800 p-4 rounded">
          <h3 class="text-xs font-bold text-gray-400 mb-2">DISAGREEMENTS QUEUE (${data.disagreements.length} items)</h3>
          <div class="space-y-2 text-xs font-mono">
            ${data.disagreements.map(d => `
              <div class="bg-gray-900 p-2 rounded flex justify-between">
                <span>Trace: ${d.trace_id}</span>
                <span>Human: <b class="${d.human_label === 'good' ? 'text-green-400':'text-red-400'}">${d.human_label}</b></span>
                <span>Judge: <b class="${d.judge_verdict === 'good' ? 'text-green-400':'text-red-400'}">${d.judge_verdict}</b></span>
              </div>
            `).join('')}
          </div>
        </div>
      `;
    }

    async function loadGates() {
      const el = document.getElementById('gates-dashboard');
      el.innerHTML = `
        <div class="bg-gray-950 border border-gray-800 p-4 rounded space-y-2 font-mono text-xs">
          <div class="text-sm font-bold text-white mb-2">Release Gate Configuration</div>
          <div class="flex justify-between border-b border-gray-800 py-2">
            <span>Rule: default_pass_rate_gate</span>
            <span class="text-yellow-400">Threshold: min 90%</span>
            <span class="text-green-400 font-bold"><i class="fa-solid fa-check mr-1"></i>ACTIVE</span>
          </div>
          <div class="flex justify-between border-b border-gray-800 py-2">
            <span>Scenario: answer_not_in_docs</span>
            <span class="text-yellow-400">Threshold: min 95%</span>
            <span class="text-green-400 font-bold"><i class="fa-solid fa-check mr-1"></i>ACTIVE</span>
          </div>
        </div>
      `;
    }

    // Initial load
    loadTraces();

    // Supabase Realtime live trace listener
    try {
      const sbUrl = 'https://qkxsetiyjqakphkzdbcf.supabase.co';
      const sbKey = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFreHNldGl5anFha3Boa3pkYmNmIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTQ3MjA5MTUsImV4cCI6MjExMDI5NjkxNX0.7b4RQymYbEHMAnZ77EtOzyK9v7qfoUS4nO0KakQmpcY';
      if (window.supabase) {
        const sbClient = window.supabase.createClient(sbUrl, sbKey);
        sbClient
          .channel('public:traces')
          .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'traces' }, payload => {
            showToast('New trace received from live app!');
            loadTraces();
          })
          .subscribe();
      }
    } catch(err) {
      console.log('Realtime setup exception:', err);
    }
  </script>
</body>
</html>
"""
