# GEMINI.md — Master Prompt: Build EvalKit

You are a senior engineer. Build **EvalKit**, a standalone evaluation toolkit and console for any LLM application: trace ingestion with SDKs, Level-1 assertions with CI gates, datasets (synthetic and real), target adapters, eval runs and comparison, a frictionless human review UI with binary labels, an LLM judge aligned to human labels (precision and recall), release gates, then A/B tests and fine-tuning export. This is its own repository and database; it shares nothing with other projects. Work autonomously; ask only when blocked with no safe default.

## Specs (source of truth)
The four documents live in `./docs/`. Import them:

@./docs/PRD.md
@./docs/TRD.md
@./docs/UI-UX.md
@./docs/BACKEND-SCHEMA.md

Precedence: BACKEND-SCHEMA > TRD > PRD > UI-UX. Log assumptions in `./docs/DECISIONS.md`.

## Scope
- In: ingest API, Python SDK (then TypeScript), redaction, trace viewer, assertion registry and built-ins, runtime-guard helper, dataset loader, synthetic generator, HTTP/CLI/Python/replay targets with fake/cached/live executors, runner, comparison with significance text, gates and `evalkit gate`, review UI, judge prompts and verdict cache, alignment report, nightly Level-2, insights, A/B framework, fine-tuning export, an instrumented example app.
- Out: hosting or building the evaluated app, automatic prompt optimisation, crowd labelling, replacing general observability platforms.

## Rules
1. **Look at data first.** Build the trace viewer and labelling flow early; do not design metrics in the abstract.
2. **Three levels:** Level 1 assertions (cheap, every change, in CI); Level 2 human and model evaluation (nightly and before minor releases); Level 3 A/B tests (after significant product changes).
3. Assertions are pure functions `(case, output, trace) -> Result`, organised by **feature** and **scenario**, and reusable as CI tests, **runtime guards**, and data-cleaning filters.
4. Human labels are **binary** (good or bad) with a one-line critique; judges output `{critique, verdict}` at temperature 0, one judge per criterion, prompts versioned and built from labelled examples.
5. **Judge alignment reports precision and recall** (bad as the positive class, plus the good class and a confusion matrix); never raw agreement alone. Keep a hold-out set out of judge prompts; require at least 50 labels before trusting a judge; re-check after any judge change.
6. **The SDK must never break the host app:** non-blocking, buffered, failures dropped and counted, under 5 ms overhead per span; test with the server down.
7. Redact secrets and configurable PII in the SDK and again on ingest; never store target credentials (reference environment variable names).
8. Every confirmed bug becomes a case. Gates are explicit rules and `evalkit gate` exits 0 pass, 1 gate failed, 2 error.
9. Provider abstraction via `ChatModel` (default Gemini through env vars); no hard-coded names or keys. Use fake and cached executors in CI; schedule live runs.
10. Python 3.11+, type hints, Pydantic v2, `ruff` + `mypy`; test the eval system itself (assertion unit tests, frozen alignment fixture, gate logic). No TODO stubs reported as done.

## Build order (Phase Report after each)
1. **Scaffold and schema:** repo layout from the TRD, Docker Compose (api, worker, postgres, redis, web), Alembic `ek_001`-`ek_005`, Makefile, CI, `.env.example`.
2. **Ingest and Python SDK:** `/api/ingest`, API keys, context managers and decorators, buffering, redaction, idempotency by `external_id`; the example app instrumented end to end.
3. **Trace viewer and review UI:** queues (unlabelled, failed assertions, judge disagrees, low rating), one-screen review, `G`/`B`/arrow shortcuts, critique, failure tags, ideal answer, add-to-dataset, time-to-label metric.
4. **Assertions and runner (Level 1):** registry and built-ins, targets (HTTP, CLI, Python callable, replay), fake/cached/live executors, `evalkit run`, pass-rate summary, `evalkit gate`, CI workflow.
5. **Datasets and synthetic generation:** loader, generator (feature, scenario, persona; dedupe by similarity; flagged for spot-check), import from traces, golden and hold-out flags.
6. **Runs and comparison UI:** per-scenario deltas, regression list, side-by-side traces, significance text with sample sizes.
7. **Judges:** prompt versioning, runner with verdict cache, alignment view and report, disagreement queue, blind-review mode.
8. **Gates and nightly Level 2:** definitions, history, CI integration; demonstrate a deliberately regressed prompt being blocked.
9. **Insights:** failure taxonomy, scenario coverage heatmap, cost per feature, review throughput.
10. **A/B framework, fine-tuning export, TypeScript SDK.**
11. **Hardening:** roles (`admin`, `evaluator`, `viewer`), retention and per-project export and delete, span partitioning, failure injection (server down must not break the host app), README.

## Definition of done
- `make test`, `make lint`, `make eval` green; the Level-1 suite for 200 cases runs under 3 minutes in CI with fakes or cache.
- Label 50 or more traces from the example app, align a judge, and show precision and recall; a regressed prompt is blocked by a gate.
- Example app instrumented with the SDK; ingest, review, run, compare, and gate flows work end to end.
- Console meets WCAG 2.2 AA (shortcuts can be disabled; live-region announcements; chart table alternatives).

## Commands (Makefile)
`make dev` · `make migrate` · `make seed` · `make test` · `make lint` · `make eval` · `make eval-nightly` · `make gate` · `make gen-cases FEATURE=... SCENARIO=... N=50` · `make align JUDGE=...` · `make example`

## Start now
Output a one-paragraph restatement, the folder tree, and the first ten tasks, then begin Step 1 and continue without waiting, reporting at the end of each step.
