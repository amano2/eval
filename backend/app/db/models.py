"""EvalKit Database Models (SQLAlchemy 2.0 compatible with PostgreSQL & SQLite)."""

import uuid
from datetime import datetime, timezone
from typing import Any, List, Optional
from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, Text, JSON,
    UniqueConstraint, Index
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

def gen_uuid() -> str:
    return str(uuid.uuid4())

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    display_name = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    disabled_at = Column(DateTime(timezone=True), nullable=True)


class Project(Base):
    __tablename__ = "projects"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    name = Column(String(255), unique=True, nullable=False)
    settings = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class ProjectMember(Base):
    __tablename__ = "project_members"

    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role = Column(String(50), default="evaluator", nullable=False)  # admin, evaluator, viewer


class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    key_hash = Column(String(255), unique=True, nullable=False)
    scopes = Column(JSON, default=lambda: ["ingest", "run"], nullable=False)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)


class PromptVersion(Base):
    __tablename__ = "prompt_versions"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    version = Column(Integer, nullable=False)
    template = Column(Text, nullable=False)
    variables = Column(JSON, default=list, nullable=False)
    notes = Column(Text, nullable=True)
    created_by = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "name", "version", name="uq_prompt_version"),
    )


class Trace(Base):
    __tablename__ = "traces"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    external_id = Column(String(255), nullable=True)
    feature = Column(String(255), nullable=True, index=True)
    scenario = Column(String(255), nullable=True, index=True)
    origin = Column(String(50), default="real", nullable=False)  # real, synthetic, eval
    tags = Column(JSON, default=list, nullable=False)
    input = Column(JSON, nullable=True)
    output = Column(JSON, nullable=True)
    model = Column(String(255), nullable=True)
    prompt_version = Column(String(255), nullable=True)
    app_version = Column(String(255), nullable=True)
    user_rating = Column(Integer, nullable=True)  # -1, 0, 1
    status = Column(String(50), default="ok", nullable=False)  # ok, error
    error = Column(Text, nullable=True)
    prompt_tokens = Column(Integer, default=0, nullable=False)
    completion_tokens = Column(Integer, default=0, nullable=False)
    cost_usd = Column(Numeric(12, 6), default=0.0, nullable=False)
    latency_ms = Column(Integer, nullable=True)
    eval_run_id = Column(String(36), nullable=True, index=True)
    started_at = Column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    ingested_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    spans = relationship("Span", back_populates="trace", cascade="all, delete-orphan")
    labels = relationship("HumanLabel", back_populates="trace", cascade="all, delete-orphan")
    verdicts = relationship("JudgeVerdict", back_populates="trace", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("project_id", "external_id", name="uq_trace_external_id"),
    )


class Span(Base):
    __tablename__ = "spans"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    trace_id = Column(String(36), ForeignKey("traces.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_span_id = Column(String(36), ForeignKey("spans.id", ondelete="CASCADE"), nullable=True)
    kind = Column(String(50), nullable=False, index=True)  # model, retrieval, tool, guardrail, assertion, judge, other
    name = Column(String(255), nullable=False, index=True)
    input = Column(JSON, nullable=True)
    output = Column(JSON, nullable=True)
    input_key = Column(String(255), nullable=True)
    output_key = Column(String(255), nullable=True)
    attributes = Column(JSON, default=dict, nullable=False)
    status = Column(String(50), default="ok", nullable=False)  # ok, error
    error = Column(Text, nullable=True)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    cost_usd = Column(Numeric(12, 6), nullable=True)
    started_at = Column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)

    trace = relationship("Trace", back_populates="spans")


class Dataset(Base):
    __tablename__ = "datasets"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    feature = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    is_golden = Column(Boolean, default=False, nullable=False)
    holdout = Column(Boolean, default=False, nullable=False)
    created_by = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    cases = relationship("Case", back_populates="dataset", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_dataset_name"),
    )


class Case(Base):
    __tablename__ = "cases"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    dataset_id = Column(String(36), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True)
    scenario = Column(String(255), nullable=False, index=True)
    input = Column(JSON, nullable=False)
    expected = Column(JSON, nullable=True)
    assertions = Column(JSON, default=list, nullable=False)  # [{"name": "has_citation", "params": {}}]
    tags = Column(JSON, default=list, nullable=False)
    source = Column(String(50), default="manual", nullable=False)  # synthetic, real_trace, manual
    source_trace_id = Column(String(36), ForeignKey("traces.id", ondelete="SET NULL"), nullable=True)
    needs_review = Column(Boolean, default=False, nullable=False)
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    dataset = relationship("Dataset", back_populates="cases")


class Target(Base):
    __tablename__ = "targets"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    kind = Column(String(50), nullable=False)  # http, cli, python, replay
    config = Column(JSON, nullable=False)
    secret_ref = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_target_name"),
    )


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    dataset_id = Column(String(36), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False)
    target_id = Column(String(36), ForeignKey("targets.id", ondelete="SET NULL"), nullable=True)
    level = Column(Integer, default=1, nullable=False)  # 1, 2, 3
    executor = Column(String(50), default="fake", nullable=False)  # fake, cached, live
    config = Column(JSON, default=dict, nullable=False)
    app_version = Column(String(255), nullable=True)
    status = Column(String(50), default="queued", nullable=False)  # queued, running, completed, failed, cancelled
    baseline_run_id = Column(String(36), ForeignKey("eval_runs.id", ondelete="SET NULL"), nullable=True)
    summary = Column(JSON, default=dict, nullable=False)
    triggered_by = Column(String(50), default="manual", nullable=False)  # ci, nightly, manual, release
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    results = relationship("EvalResult", back_populates="eval_run", cascade="all, delete-orphan")


class EvalResult(Base):
    __tablename__ = "eval_results"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    eval_run_id = Column(String(36), ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    case_id = Column(String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False)
    trace_id = Column(String(36), ForeignKey("traces.id", ondelete="SET NULL"), nullable=True)
    output = Column(JSON, nullable=True)
    assertion_results = Column(JSON, default=list, nullable=False)  # [{"name": str, "passed": bool, "detail": str}]
    passed = Column(Boolean, nullable=True, index=True)
    judge_verdict_id = Column(String(36), nullable=True)
    cost_usd = Column(Numeric(10, 6), default=0.0, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    eval_run = relationship("EvalRun", back_populates="results")
    case = relationship("Case")

    __table_args__ = (
        UniqueConstraint("eval_run_id", "case_id", name="uq_eval_result_case"),
    )


class FailureTag(Base):
    __tablename__ = "failure_tags"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_failure_tag_name"),
    )


class HumanLabel(Base):
    __tablename__ = "human_labels"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    trace_id = Column(String(36), ForeignKey("traces.id", ondelete="CASCADE"), nullable=False, index=True)
    labeler_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    criterion = Column(String(100), default="overall", nullable=False)
    label = Column(String(20), nullable=False)  # good, bad
    critique = Column(Text, nullable=True)
    ideal_output = Column(Text, nullable=True)
    failure_tags = Column(JSON, default=list, nullable=False)
    blind = Column(Boolean, default=False, nullable=False)
    seconds_spent = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    trace = relationship("Trace", back_populates="labels")

    __table_args__ = (
        UniqueConstraint("trace_id", "labeler_id", "criterion", name="uq_human_label_trace_criterion"),
    )


class Judge(Base):
    __tablename__ = "judges"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    criterion = Column(String(100), nullable=False)
    prompt_version_id = Column(String(36), ForeignKey("prompt_versions.id"), nullable=False)
    model = Column(String(255), nullable=False)
    min_labels_required = Column(Integer, default=50, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_judge_name"),
    )


class JudgeVerdict(Base):
    __tablename__ = "judge_verdicts"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    judge_id = Column(String(36), ForeignKey("judges.id", ondelete="CASCADE"), nullable=False, index=True)
    trace_id = Column(String(36), ForeignKey("traces.id", ondelete="CASCADE"), nullable=False, index=True)
    verdict = Column(String(20), nullable=False)  # good, bad
    critique = Column(Text, nullable=True)
    input_hash = Column(String(64), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    trace = relationship("Trace", back_populates="verdicts")

    __table_args__ = (
        UniqueConstraint("judge_id", "trace_id", name="uq_judge_verdict_trace"),
    )


class Gate(Base):
    __tablename__ = "gates"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    dataset_id = Column(String(36), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=True)
    scope = Column(JSON, default=dict, nullable=False)  # {"feature": "qa", "scenario": "answer_in_docs"}
    metric = Column(String(100), default="pass_rate", nullable=False)
    threshold = Column(Numeric(6, 4), nullable=False)
    rule = Column(String(50), default="min", nullable=False)  # min, max, no_significant_drop
    level = Column(Integer, default=1, nullable=False)
    blocking = Column(Boolean, default=True, nullable=False)
    reason = Column(Text, nullable=True)
    updated_by = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_gate_name"),
    )


class GateResult(Base):
    __tablename__ = "gate_results"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    eval_run_id = Column(String(36), ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    gate_id = Column(String(36), ForeignKey("gates.id", ondelete="CASCADE"), nullable=False)
    status = Column(String(20), default="pending", nullable=False)  # pass, fail, pending, error
    observed = Column(Numeric(6, 4), nullable=True)
    baseline = Column(Numeric(6, 4), nullable=True)
    detail = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("eval_run_id", "gate_id", name="uq_gate_result_run_gate"),
    )


class Experiment(Base):
    __tablename__ = "experiments"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    key = Column(String(255), nullable=False)
    variants = Column(JSON, default=list, nullable=False)  # [{"name": "A", "weight": 50, "config": {}}]
    primary_metric = Column(String(100), nullable=False)
    guard_metrics = Column(JSON, default=list, nullable=False)
    min_sample = Column(Integer, default=200, nullable=False)
    status = Column(String(50), default="draft", nullable=False)  # draft, running, stopped, completed
    started_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "key", name="uq_experiment_key"),
    )


class ExperimentAssignment(Base):
    __tablename__ = "experiment_assignments"

    experiment_id = Column(String(36), ForeignKey("experiments.id", ondelete="CASCADE"), primary_key=True)
    subject_id = Column(String(255), primary_key=True)
    variant = Column(String(100), nullable=False)
    assigned_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class FinetuneExport(Base):
    __tablename__ = "finetune_exports"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    filter = Column(JSON, default=dict, nullable=False)
    example_count = Column(Integer, default=0, nullable=False)
    storage_key = Column(String(255), nullable=False)
    provenance = Column(JSON, default=list, nullable=False)  # list of trace IDs
    created_by = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    actor_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action = Column(String(100), nullable=False)
    target_type = Column(String(100), nullable=True)
    target_id = Column(String(255), nullable=True)
    details = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
