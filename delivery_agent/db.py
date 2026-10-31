"""Relational storage (SQLAlchemy 2.0). SQLite by default; set DATABASE_URL to postgresql+psycopg://... to scale out.

Stores what used to live only in memory / JSON files: chat runs, uploaded documents, tasks (versioned, with
their Jira key once pushed), every plan version, approvals, push results, the audit log, tool calls, rule
versions, and caches for AI rule readings and embeddings.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .models import Task

ROOT = Path(__file__).resolve().parent.parent


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class RunRow(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(200), default="")
    project_key: Mapped[str] = mapped_column(String(40), default="", index=True)
    profile: Mapped[str] = mapped_column(String(40), default="")
    run_dir: Mapped[str] = mapped_column(Text, default="")
    plan_options: Mapped[dict] = mapped_column(JSON, default=dict)
    offline: Mapped[int] = mapped_column(Integer, default=0)
    chat: Mapped[dict] = mapped_column(JSON, default=dict)          # {"messages": [...], "display": [...]}
    created_by: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class DocumentRow(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(20))        # doc | excel | zip
    path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class TaskRow(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    ext_id: Mapped[str] = mapped_column(String(100))     # T001 / #123 / Sheet!R5 - the id shown to people
    position: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[dict] = mapped_column(JSON)             # Task.dump()
    version: Mapped[int] = mapped_column(Integer, default=1)
    jira_key: Mapped[str] = mapped_column(String(40), default="")
    deleted: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class PlanRow(Base):
    __tablename__ = "plans"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    plan_hash: Mapped[str] = mapped_column(String(64), index=True)
    plan: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ApprovalRow(Base):
    __tablename__ = "approvals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    plan_hash: Mapped[str] = mapped_column(String(64), index=True)
    decision: Mapped[str] = mapped_column(String(20))
    approver: Mapped[str] = mapped_column(String(100))
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PushResultRow(Base):
    __tablename__ = "push_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    plan_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AuditRow(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32), index=True, default="")
    step: Mapped[str] = mapped_column(String(40), index=True)
    entry: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ToolCallRow(Base):
    __tablename__ = "tool_calls"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(60))
    args: Mapped[dict] = mapped_column(JSON)
    ok: Mapped[int] = mapped_column(Integer)
    error: Mapped[str] = mapped_column(Text, default="")
    duration_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RuleVersionRow(Base):
    __tablename__ = "rule_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project: Mapped[str] = mapped_column(String(40), index=True, default="")   # "" = common rules
    text: Mapped[str] = mapped_column(Text)
    author: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CacheRow(Base):
    __tablename__ = "cache"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)   # "<kind>:<sha256>"
    value: Mapped[dict | list] = mapped_column(JSON)


def default_url() -> str:
    url = os.getenv("DATABASE_URL", "sqlite:///data/app.db")
    if url.startswith("sqlite:///") and not url.startswith("sqlite:////") and url != "sqlite:///:memory:":
        path = Path(url.removeprefix("sqlite:///"))
        if not path.is_absolute():
            path = ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{path.as_posix()}"
    return url


class KVCache:
    """Key/value cache on the `cache` table, namespaced by kind (assessment, embedding)."""

    def __init__(self, repo: "Repository", kind: str):
        self.repo, self.kind = repo, kind

    def get_many(self, keys: list[str]) -> dict:
        if not keys:
            return {}
        full = {f"{self.kind}:{k}": k for k in keys}
        with self.repo.session() as s:
            rows = s.scalars(select(CacheRow).where(CacheRow.key.in_(list(full)))).all()
            return {full[r.key]: r.value for r in rows}

    def put_many(self, items: dict) -> None:
        with self.repo.session() as s, s.begin():
            for k, v in items.items():
                s.merge(CacheRow(key=f"{self.kind}:{k}", value=v))


class Repository:
    def __init__(self, url: str | None = None):
        url = url or default_url()
        kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
        if url in ("sqlite://", "sqlite:///:memory:"):
            from sqlalchemy.pool import StaticPool
            kwargs["poolclass"] = StaticPool   # one shared in-memory database
        self.engine = create_engine(url, **kwargs)
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(self.engine, expire_on_commit=False)

    # ---------------------------------------------------------------- runs
    def create_run(self, project_key: str, profile: str, run_dir: Path, created_by: str = "") -> str:
        with self.session() as s, s.begin():
            row = RunRow(project_key=project_key, profile=profile, run_dir=str(run_dir), created_by=created_by)
            s.add(row)
        return row.id

    def update_run(self, run_id: str, **fields) -> None:
        with self.session() as s, s.begin():
            row = s.get(RunRow, run_id)
            for k, v in fields.items():
                setattr(row, k, v)

    def get_run(self, run_id: str) -> RunRow | None:
        with self.session() as s:
            return s.get(RunRow, run_id)

    def list_runs(self, project_key: str | None = None, limit: int = 20) -> list[dict]:
        with self.session() as s:
            q = select(RunRow).order_by(RunRow.updated_at.desc()).limit(limit)
            if project_key:
                q = q.where(RunRow.project_key == project_key)
            runs = s.scalars(q).all()
            out = []
            for r in runs:
                plan = s.scalars(select(PlanRow).where(PlanRow.run_id == r.id).order_by(PlanRow.version.desc())).first()
                n_tasks = len(s.scalars(select(TaskRow.id).where(TaskRow.run_id == r.id, TaskRow.deleted == 0)).all())
                out.append({"run_id": r.id, "title": r.title, "project_key": r.project_key, "profile": r.profile,
                            "tasks": n_tasks, "plan_version": plan.version if plan else 0,
                            "updated_at": r.updated_at.isoformat(timespec="seconds") if r.updated_at else ""})
            return out

    # ---------------------------------------------------------------- documents
    def add_document(self, run_id: str, filename: str, sha256: str, kind: str, path: Path) -> None:
        with self.session() as s, s.begin():
            s.add(DocumentRow(run_id=run_id, filename=filename, sha256=sha256, kind=kind, path=str(path)))

    def documents(self, run_id: str) -> list[DocumentRow]:
        with self.session() as s:
            return list(s.scalars(select(DocumentRow).where(DocumentRow.run_id == run_id)).all())

    # ---------------------------------------------------------------- tasks
    def replace_tasks(self, run_id: str, tasks: list[Task]) -> None:
        """Store the run's task list: rows are matched by ext_id; changed rows get version+1, missing ones are
        marked deleted (kept for history)."""
        with self.session() as s, s.begin():
            rows = {r.ext_id: r for r in s.scalars(select(TaskRow).where(TaskRow.run_id == run_id)).all()}
            seen = set()
            for pos, t in enumerate(tasks):
                data = t.dump()
                seen.add(t.id)
                row = rows.get(t.id)
                if row is None:
                    s.add(TaskRow(run_id=run_id, ext_id=t.id, position=pos, data=data, jira_key=t.jira_key))
                    continue
                if row.data != data or row.deleted:
                    row.version += 1
                    row.data, row.deleted = data, 0
                row.position, row.jira_key = pos, t.jira_key
            for ext_id, row in rows.items():
                if ext_id not in seen:
                    row.deleted = 1

    def load_tasks(self, run_id: str) -> list[Task]:
        with self.session() as s:
            rows = s.scalars(select(TaskRow).where(TaskRow.run_id == run_id, TaskRow.deleted == 0)
                             .order_by(TaskRow.position)).all()
            return [Task(**r.data) for r in rows]

    def task_versions(self, run_id: str) -> dict[str, int]:
        with self.session() as s:
            return {r.ext_id: r.version for r in s.scalars(select(TaskRow).where(TaskRow.run_id == run_id)).all()}

    # ---------------------------------------------------------------- plans / approvals / push
    def save_plan(self, run_id: str, plan: dict, plan_hash: str) -> int:
        with self.session() as s, s.begin():
            last = s.scalars(select(PlanRow).where(PlanRow.run_id == run_id).order_by(PlanRow.version.desc())).first()
            if last and last.plan_hash == plan_hash:
                return last.version
            version = (last.version if last else 0) + 1
            s.add(PlanRow(run_id=run_id, version=version, plan_hash=plan_hash, plan={**plan, "version": version}))
            s.get(RunRow, run_id).updated_at = _now()
        return version

    def latest_plan(self, run_id: str) -> dict | None:
        with self.session() as s:
            row = s.scalars(select(PlanRow).where(PlanRow.run_id == run_id).order_by(PlanRow.version.desc())).first()
            return row.plan if row else None

    def plan_versions(self, run_id: str) -> int:
        with self.session() as s:
            row = s.scalars(select(PlanRow).where(PlanRow.run_id == run_id).order_by(PlanRow.version.desc())).first()
            return row.version if row else 0

    def record_approval(self, run_id: str, decision: dict) -> None:
        with self.session() as s, s.begin():
            s.add(ApprovalRow(run_id=run_id, plan_hash=decision["plan_hash"], decision=decision["decision"],
                              approver=decision["approver"], note=decision.get("note", "")))

    def save_push_state(self, run_id: str, plan_hash: str, state: dict) -> None:
        with self.session() as s, s.begin():
            s.add(PushResultRow(run_id=run_id, plan_hash=plan_hash, state=state))

    def set_jira_keys(self, run_id: str, keys: dict[str, str]) -> None:
        """task ext_id -> Jira key, after a push."""
        with self.session() as s, s.begin():
            for r in s.scalars(select(TaskRow).where(TaskRow.run_id == run_id, TaskRow.ext_id.in_(list(keys)))).all():
                r.jira_key = keys[r.ext_id]
                r.data = {**r.data, "jiraKey": keys[r.ext_id], "pendingUpdate": []}

    # ---------------------------------------------------------------- audit / tools / rules
    def add_audit(self, run_id: str, entry: dict) -> None:
        with self.session() as s, s.begin():
            s.add(AuditRow(run_id=run_id, step=entry.get("step", ""), entry=entry))

    def audit(self, run_id: str) -> list[dict]:
        with self.session() as s:
            return [r.entry for r in s.scalars(select(AuditRow).where(AuditRow.run_id == run_id).order_by(AuditRow.id)).all()]

    def add_tool_call(self, run_id: str, name: str, args: dict, ok: bool, error: str, duration_ms: int) -> None:
        with self.session() as s, s.begin():
            s.add(ToolCallRow(run_id=run_id, name=name, args=json.loads(json.dumps(args, default=str)), ok=int(ok),
                              error=error[:2000], duration_ms=duration_ms))

    def tool_calls(self, run_id: str) -> list[dict]:
        with self.session() as s:
            return [{"name": r.name, "args": r.args, "ok": bool(r.ok), "error": r.error, "duration_ms": r.duration_ms}
                    for r in s.scalars(select(ToolCallRow).where(ToolCallRow.run_id == run_id).order_by(ToolCallRow.id)).all()]

    def add_rule_version(self, project: str, text: str, author: str = "") -> None:
        with self.session() as s, s.begin():
            s.add(RuleVersionRow(project=project or "", text=text, author=author))

    def cache(self, kind: str) -> KVCache:
        return KVCache(self, kind)


_default: Repository | None = None


def get_repository() -> Repository:
    """Process-wide repository for DATABASE_URL (Streamlit reruns and the CLI share it)."""
    global _default
    if _default is None:
        _default = Repository()
    return _default
