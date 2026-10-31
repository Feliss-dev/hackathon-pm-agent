"""Chat agent: the LLM decides which analysis tools to run from the user's prompt.

Tools are declared in `tools.py` (typed registry). This module holds the session state (persisted in the database,
indexed in the vector store) and the function-calling loop. The agent has NO tool that writes to Jira: approval and
push stay as buttons a human clicks.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import os
import zipfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from .ai_rules import RuleAssessor
from .approval import plan_hash
from .audit import AuditLog
from .config import combine_rules, load_rules, read_common_rules, read_project_rules
from .db import Repository, get_repository
from .excel_io import read_tasks, update_product_column
from .extract import document_text, extract
from .llm import chat_model, get_client, with_retry
from .models import Task
from .planner import build_plan, render_markdown, render_tree
from .sync import apply_sync, diff_tasks
from .workshop import validate_package

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
MAX_TOOL_ROUNDS = 8
EVIDENCE_MIN_SCORE = 0.75     # similar past tasks shown to the rule assessor
COMMON_RULES_KEY = "__common__"
DOC_SUFFIXES = (".md", ".txt", ".docx", ".pdf")
log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Delivery Planning Agent, an AI assistant for project managers. You turn project documents
and Excel task lists into a reviewed Jira plan by calling tools. Answer in the user's language (usually Vietnamese).

How to work:
- Act, don't interrogate. When the user asks for something a tool can do, call the tool right away with sensible
  defaults, then explain the result. Ask a question only when a required input is truly missing (e.g. no file).
- To analyse, call `analyze` with the file names from `list_files` (attached files are listed in CURRENT STATE).
  Use the Epic key the user gave (e.g. KAN-1); if none, analyse without an Epic and mention it.
- After analysing, summarise briefly: number of tasks, warnings, duplicates, what will be created/skipped, and the
  2-3 most important problems with a concrete fix (e.g. "apply suggested estimates", "assign X to Y").
- You may change the plan with `update_tasks`, `apply_suggested_estimates` and `sync_excel` (apply=true) when the user
  asks. Any change creates a new plan version that must be approved again.
- When the user uploads an edited Excel for tasks that were already analysed, call `sync_excel` with apply=false,
  show the differences as a table, and apply only after the user agrees.
- Questions about what the requirement documents say: call `ask_requirements` and answer only from the returned
  passages, citing the file name. "Have we done this before / how long did it take": call `search_similar_tasks`.
- A plan built in offline mode was not checked against Jira; before pushing, call `recheck_with_jira`.
- You cannot approve the plan or push it. When the user wants to push the plan, call `prepare_jira_push` and tell
  them to review the panel on the left and click "Approve" then "Đẩy lên Jira". Never claim issues were created
  unless the CURRENT STATE shows push results.
- Tools named `jira_*` are the Jira MCP server's own tools (issues, sprints, boards, links, transitions, comments,
  worklogs, versions...). Read tools run at once. Write tools (create, update, delete, assign, transition, comment,
  link, sprint changes) are NOT executed: they return status "pending_approval" with an action_id. Tell the user which
  action is waiting and that they must approve it in the "Jira actions" panel; say it was done only after the result
  shows it was executed. Prefer the plan flow (`analyze` -> approval -> push) for creating many issues from documents.
- Categories and estimate suggestions come from the project's human-written rules (config/rules.md + the
  project's .md), applied per task; the plan stores the reason for each. Required-field and duplicate checks are
  done by code. Explain decisions using the tool output and those reasons; never invent numbers, task names or
  Jira keys. If the user disagrees with a rule, suggest how to reword it in "⚙️ Xem / sửa rule".
  Never reveal API keys or tokens.
- Format: short paragraphs, bullet lists, small markdown tables when listing tasks. No long preambles."""


class AgentSession:
    """Everything the agent has produced in one chat session (= one `runs` row in the database)."""

    def __init__(self, config: dict, offline: bool = False, project: str = "", rules_text: str | None = None,
                 assessor=None, repo: Repository | None = None, vectors=None, run_id: str | None = None,
                 created_by: str = "", out_root: Path | None = None, jira_bridge=None):
        self.config = copy.deepcopy(config)
        self._jira_bridge = jira_bridge   # explicit MCP connection (tests, notebook); else the shared one from .env
        self._jira_toolbox = None
        self.jira_warning = ""
        self.rag_llm = None               # LangChain chat model for ask_requirements(answer=True); None = the gateway
        self.project = project          # project profile ("" = common rules + settings.yaml only)
        self.repo = repo or get_repository()
        self._vectors = vectors         # None: default store, created lazily; False: vector lookup disabled
        self.vector_warning = ""
        self.offline = offline
        self.rules_text = rules_text if rules_text is not None else load_rules(project or None)
        self._custom_assessor = assessor is not None
        self.existing_for = None        # project key the Jira duplicate list was loaded for
        self.files: dict[str, Path] = {}
        self.tasks: list[Task] = []
        self.plan: dict | None = None
        self.plan_options: dict = {}
        self.existing: list = []
        self.downloads: dict[str, Path] = {}
        self.workshop_report: dict | None = None
        self.push_state: dict | None = None
        self.sync_report: dict | None = None
        self.approval_requested = False
        self.last_error = ""
        if run_id:
            self._load(run_id)
        else:
            self.run_dir = (out_root or ROOT / "out" / "chat") / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            (self.run_dir / "inputs").mkdir(parents=True, exist_ok=True)
            self.run_id = self.repo.create_run(self.project_key, project, self.run_dir, created_by)
            self.repo.update_run(self.run_id, offline=int(offline))
            self.audit = self._audit_log()
        self.assessor = assessor or self._make_assessor()

    # -------------------------------------------------------------- wiring
    @property
    def project_key(self) -> str:
        return self.config["jira"]["project_key"]

    @property
    def plan_path(self) -> Path:
        return self.run_dir / "plan.json"

    @property
    def vectors(self):
        if self._vectors is None:
            try:
                from .vectors import get_vector_store
                self._vectors = get_vector_store()
            except Exception as exc:
                self._vector_failed(exc)
        return self._vectors or None

    @property
    def jira_toolbox(self):
        """The Jira MCP tools for the agent, or None when no Jira connection is configured / offline mode is on."""
        if self._jira_toolbox is None:
            configured = self._jira_bridge is not None or (
                not self.offline and (os.getenv("JIRA_MCP_URL") or os.getenv("JIRA_MCP_COMMAND")))
            if not configured:
                return None
            try:
                from .jira_tools import JiraToolbox, shared_bridge
                bridge = self._jira_bridge.start() if self._jira_bridge is not None else shared_bridge(self.config)
                self._jira_toolbox = JiraToolbox(bridge, self.config["jira"].get("agent_write_policy", "approve"),
                                                 audit=lambda step, **kw: self.audit.record(step, **kw))
                self.jira_warning = ""
            except Exception as exc:
                self._jira_toolbox = False
                self.jira_warning = f"Jira MCP tools unavailable: {type(exc).__name__}: {str(exc)[:200]}"
                log.warning(self.jira_warning)
        return self._jira_toolbox or None

    def agent_tool_schemas(self) -> list[dict]:
        from .tools import REGISTRY
        jira = self.jira_toolbox
        return REGISTRY.schemas() + (jira.schemas() if jira else [])

    def pending_jira_actions(self) -> list[dict]:
        return self._jira_toolbox.pending() if self._jira_toolbox else []

    def decide_jira_action(self, action_id: str, approve: bool, approver: str = "") -> dict:
        if not self._jira_toolbox:
            return {"error": "No Jira connection"}
        return self._jira_toolbox.decide(action_id, approve, approver)

    def _vector_failed(self, exc: Exception) -> None:
        self.vector_warning = f"Vector lookup unavailable: {type(exc).__name__}: {str(exc)[:200]}"
        log.warning(self.vector_warning)

    def _vec(self, fn: Callable, default=None):
        """Run a vector-store operation; the plan never fails because embeddings are down."""
        store = self.vectors
        if not store:
            return default
        try:
            result = fn(store)
            self.vector_warning = ""
            return result
        except Exception as exc:
            self._vector_failed(exc)
            return default

    def _audit_log(self) -> AuditLog:
        return AuditLog(self.run_dir / "audit.jsonl", sink=lambda e: self.repo.add_audit(self.run_id, e))

    def _make_assessor(self) -> RuleAssessor:
        return RuleAssessor(self.rules_text, hours_per_day=self.config["estimation"].get("hours_per_day", 8),
                            cache=self.repo.cache("assessment"), evidence=self._evidence, rules_for=self._rules_for,
                            on_retry=lambda msg: self.audit.record("llm_retry", reason=msg))

    def _evidence(self, tasks: list[Task]) -> dict[str, list[dict]]:
        hits = self._vec(lambda v: v.similar_tasks(self.project_key, [f"{t.title}\n{t.description}" for t in tasks],
                                                   k=3, exclude_run=self.run_id), [])
        out = {}
        for t, row in zip(tasks, hits or []):
            similar = [{"title": h.meta.get("title", ""), "category": h.meta.get("category", ""),
                        "estimate": h.meta.get("estimate", ""), "similarity": round(h.score, 2)}
                       for h in row if h.score >= EVIDENCE_MIN_SCORE]
            if similar:
                out[t.id] = similar
        return out

    def _rules_for(self, tasks: list[Task]) -> str:
        """Project rules in full (they override) + only the common rules relevant to these tasks."""
        common = read_common_rules()

        def pick(v):
            v.index_rules(COMMON_RULES_KEY, common)
            return v.relevant_rules(COMMON_RULES_KEY, [f"{t.title}\n{t.description}" for t in tasks])
        chunks = self._vec(pick, None)
        if not chunks:
            return self.rules_text
        return combine_rules("\n\n".join(chunks), read_project_rules(self.project), self.project)

    def record_tool_call(self, name: str, args: dict, result: dict, duration_ms: int) -> None:
        ok = "error" not in result
        self.audit.record("agent_tool", reason=f"{name}({json.dumps(args, ensure_ascii=False, default=str)[:300]})",
                          data={"ok": ok, "ms": duration_ms})
        self.repo.add_tool_call(self.run_id, name, args, ok, result.get("error", ""), duration_ms)

    # -------------------------------------------------------------- persistence
    def _load(self, run_id: str) -> None:
        run = self.repo.get_run(run_id) or next(
            (self.repo.get_run(r["run_id"]) for r in self.repo.list_runs(limit=500) if r["run_id"].startswith(run_id)), None)
        if not run:
            raise KeyError(f"Run '{run_id}' not found. Use list_runs.")
        self.run_id, self.run_dir = run.id, Path(run.run_dir)
        (self.run_dir / "inputs").mkdir(parents=True, exist_ok=True)
        self.audit = self._audit_log()
        self.plan_options = dict(run.plan_options or {})
        self.files = {d.filename: Path(d.path) for d in self.repo.documents(run.id) if Path(d.path).exists()}
        self.tasks = self.repo.load_tasks(run.id)
        self.plan = self.repo.latest_plan(run.id)
        if self.plan:
            self.plan_path.write_text(json.dumps(self.plan, indent=2, ensure_ascii=False), encoding="utf-8")
            (self.run_dir / "plan.md").write_text(render_markdown(self.plan), encoding="utf-8")
            self.downloads["plan.md"] = self.run_dir / "plan.md"
        result = self.plan_path.with_name("plan.result.json")
        if result.exists():
            self.push_state = json.loads(result.read_text(encoding="utf-8"))

    def open_run(self, run_id: str) -> dict:
        """Switch this chat to an earlier run."""
        self.existing, self.existing_for, self.push_state, self.sync_report = [], None, None, None
        self.downloads, self.workshop_report = {}, None
        self._load(run_id)
        out = {"run_id": self.run_id, "files": sorted(self.files), "tasks": len(self.tasks)}
        return out | (self.show_plan("summary") if self.plan else {"plan": None})

    def save_chat(self, messages: list[dict], display: list[dict]) -> None:
        title = next((m["content"][:80] for m in display if m.get("role") == "user"), "")
        self.repo.update_run(self.run_id, chat={"messages": messages, "display": display},
                             **({"title": title} if title else {}))

    def load_chat(self) -> tuple[list[dict], list[dict]]:
        chat = (self.repo.get_run(self.run_id).chat or {})
        return list(chat.get("messages", [])), list(chat.get("display", []))

    # -------------------------------------------------------------- files
    def add_file(self, name: str, data: bytes) -> Path:
        path = self.run_dir / "inputs" / Path(name).name
        path.write_bytes(data)
        self.files[path.name] = path
        suffix = path.suffix.lower()
        kind = "excel" if suffix in (".xlsx", ".xlsm") else "zip" if suffix == ".zip" else "doc"
        self.repo.add_document(self.run_id, path.name, hashlib.sha256(data).hexdigest(), kind, path)
        if suffix in DOC_SUFFIXES:
            self._index_document(path)
        return path

    def _index_document(self, path: Path) -> None:
        try:
            text = document_text(path)
        except Exception as exc:
            self.audit.record("doc_index", reason=f"Cannot read {path.name}: {exc}")
            return
        n = self._vec(lambda v: v.index_document(self.project_key, self.run_id, path.name, text), 0)
        self.audit.record("doc_index", reason=f"{path.name}: {n} chunks indexed" if n else
                          f"{path.name}: not indexed ({self.vector_warning or 'vector lookup off'})")

    def _resolve(self, name: str) -> Path:
        if name in self.files:
            return self.files[name]
        if Path(name).is_file():   # CLI: a real path
            return Path(name)
        candidate = SAMPLES / Path(name).name
        if candidate.exists():
            return candidate
        for key, path in self.files.items():  # tolerate partial names
            if name.lower() in key.lower():
                return path
        raise FileNotFoundError(f"File '{name}' not found. Use list_files.")

    # -------------------------------------------------------------- tools
    def list_files(self) -> dict:
        samples = sorted(p.name for p in SAMPLES.glob("*") if p.suffix.lower() in (".md", ".xlsx", ".docx", ".pdf", ".zip"))
        return {"attached": sorted(self.files), "samples": samples}

    def analyze(self, files: list[str], epic_key: str = "", new_epic: str = "", sprint_id: int | None = None,
                include_duplicates: bool = False, allow_incomplete: bool = False) -> dict:
        paths = [self._resolve(f) for f in files]
        for p in paths:   # sample docs chosen by name are indexed too, so ask_requirements can use them
            if p.suffix.lower() in DOC_SUFFIXES and p.name not in self.files:
                self.files[p.name] = p
                self._index_document(p)
        self.tasks = extract(paths, self.config)
        self.existing = []
        if not self.offline:
            from .jira_mcp import load_existing
            self.existing = asyncio.run(load_existing(self.config, epic_key))
            self.existing_for = self.project_key
        self.plan_options = dict(epic_key=epic_key, new_epic="" if epic_key else new_epic, sprint_id=sprint_id,
                                 include_duplicates=include_duplicates, allow_incomplete=allow_incomplete)
        self.repo.update_run(self.run_id, plan_options=self.plan_options)
        self._rebuild()
        return self.show_plan("summary") | {"skipped": self.show_plan("skipped")["skipped"]}

    def recheck_with_jira(self) -> dict:
        """Leave offline mode and re-run duplicate detection against Jira for the current plan."""
        self._need_plan()
        from .jira_mcp import load_existing
        self.offline = False
        self.existing = asyncio.run(load_existing(self.config, self.plan_options.get("epic_key", "")))
        self.existing_for = self.project_key
        self._rebuild()
        return {"jira_issues_compared": len(self.existing)} | self.show_plan("summary")

    def _history(self) -> dict[str, list[dict]]:
        """Nearest tasks of EARLIER runs of this project, for the semantic duplicate check."""
        hits = self._vec(lambda v: v.similar_tasks(self.project_key, [f"{t.title}\n{t.description}" for t in self.tasks],
                                                   k=3, exclude_run=self.run_id), [])
        return {t.id: [{"title": h.meta.get("title", ""), "score": h.score, "jira_key": h.meta.get("jira_key", ""),
                        "run_id": h.meta.get("run_id", ""), "task_id": h.meta.get("task_id", "")} for h in row]
                for t, row in zip(self.tasks, hits or [])}

    def _rebuild(self) -> None:
        jira_checked = not self.offline and self.existing_for == self.project_key
        self.plan = build_plan(self.tasks, self.config, self.audit, assessor=self.assessor, existing=self.existing,
                               jira_checked=jira_checked, history=self._history(), **self.plan_options)
        # store: tasks (versioned), plan version, vectors with the final category for later lookups
        self.repo.replace_tasks(self.run_id, self.tasks)
        self.plan["version"] = self.repo.save_plan(self.run_id, self.plan, plan_hash(self.plan))
        self.plan_path.write_text(json.dumps(self.plan, indent=2, ensure_ascii=False), encoding="utf-8")
        (self.run_dir / "plan.md").write_text(render_markdown(self.plan), encoding="utf-8")
        self.downloads["plan.md"] = self.run_dir / "plan.md"
        self.push_state = None
        hpd = self.config["estimation"].get("hours_per_day", 8)
        self._vec(lambda v: v.index_tasks(self.project_key, self.run_id, self.tasks, hpd))

    def apply_config(self, config: dict, project: str = "", rules_text: str | None = None) -> None:
        """Switch settings and rules; an existing plan is rebuilt (re-classified, re-reviewed) with them."""
        if config["jira"]["project_key"] != self.project_key:
            self.existing, self.existing_for = [], None   # Jira duplicates belong to the old project
        self.config, self.project = copy.deepcopy(config), project
        self.repo.update_run(self.run_id, project_key=self.project_key, profile=project)
        rules_text = rules_text if rules_text is not None else load_rules(project or None)
        if rules_text != self.rules_text:
            self.rules_text = rules_text
            if not self._custom_assessor:
                self.assessor = self._make_assessor()
            else:
                self.assessor = RuleAssessor(rules_text, hours_per_day=self.config["estimation"].get("hours_per_day", 8))
        self.audit.record("rules_changed", reason=f"Rules profile: {project or 'default'}")
        if self.tasks and self.plan:
            self._rebuild()

    def _need_plan(self) -> dict:
        if not self.plan:
            raise RuntimeError("No plan yet - run analyze first.")
        return self.plan

    def show_plan(self, section: str) -> dict:
        p = self._need_plan()
        if section == "summary":
            return {"epic": p["epic_key"] or p["new_epic"] or None, "jira_checked": p["jira_duplicate_check"],
                    "plan_version": p.get("version"), "summary": p["summary"]["lines"],
                    "by_category": p["summary"]["by_category"]}
        if section == "tree":
            return {"tree": "\n".join(render_tree(p))}
        if section == "tasks":
            return {"tasks": [{k: t.get(k, "") for k in ("id", "title", "category", "estimate", "assignee", "story", "jiraKey")}
                              for t in p["tasks"]]}
        if section == "estimate_warnings":
            return {"estimate_warnings": p["estimate_findings"]}
        if section == "validation_issues":
            return {"validation_issues": [{k: i[k] for k in ("task_id", "task", "kind", "severity", "detail")} for i in p["validation_issues"]]}
        if section == "skipped":
            return {"skipped": [{"task_id": n["task_id"], "summary": n["summary"], "reason": n["skip_reason"]}
                                for n in p["planned_issues"] if n["skip_reason"]]}
        raise ValueError(f"Unknown section {section}")

    def update_tasks(self, updates: list[dict]) -> dict:
        self._need_plan()
        by_id = {t.id: t for t in self.tasks}
        changed = []
        for u in updates:
            task = by_id.get(u["task_id"])
            if not task:
                raise KeyError(f"Unknown task_id {u['task_id']}")
            old = getattr(task, u["field"])
            setattr(task, u["field"], u["value"])
            if task.jira_key and u["field"] != "story" and u["field"] not in task.pending_update:
                task.pending_update = sorted(task.pending_update + [u["field"]])
            changed.append({"task_id": task.id, "field": u["field"], "old": old, "new": u["value"]})
            self.audit.record("agent_edit", task=task.title, task_id=task.id,
                              reason=f"{u['field']}: '{old}' -> '{u['value']}' (requested in chat)")
        self._rebuild()
        return {"changed": changed} | self.show_plan("summary")

    def apply_suggested_estimates(self, only_missing: bool = True, task_ids: list[str] | None = None) -> dict:
        p = self._need_plan()
        updates = []
        for f in p["estimate_findings"]:
            if not f["suggestedEstimate"] or (task_ids and f["task_id"] not in task_ids):
                continue
            if only_missing and f["rule"] != "missing_estimate":
                continue
            updates.append({"task_id": f["task_id"], "field": "estimate", "value": f["suggestedEstimate"]})
        if not updates:
            return {"changed": [], "message": "No matching estimate suggestions."}
        return self.update_tasks(list({u["task_id"]: u for u in updates}.values()))

    def assess_task(self, title: str, description: str = "", assignee: str = "", estimate: str = "") -> dict:
        task = Task(id="probe", title=title, description=description, assignee=assignee, estimate=estimate)
        return self.assessor.assess([task])["probe"].dump()

    def search_similar_tasks(self, text: str, k: int = 5) -> dict:
        hits = self._vec(lambda v: v.similar_tasks(self.project_key, [text], k=k), None)
        if hits is None:
            return {"error": self.vector_warning or "Vector lookup is turned off"}
        return {"similar_tasks": [{"title": h.meta.get("title"), "category": h.meta.get("category"),
                                   "estimate": h.meta.get("estimate"), "jira_key": h.meta.get("jira_key"),
                                   "run": h.meta.get("run_id", "")[:8], "this_run": h.meta.get("run_id") == self.run_id,
                                   "similarity": round(h.score, 3)} for h in hits[0]]}

    def ask_requirements(self, question: str, k: int = 5, all_runs: bool = False, answer: bool = False) -> dict:
        """Passages of the uploaded documents (LangChain retriever over the vector store); with `answer=True` also a
        grounded answer with citations from the LCEL chain."""
        from .lc import ProjectRetriever
        run_id = None if all_runs else self.run_id
        docs = self._vec(lambda v: ProjectRetriever(store=v, project_key=self.project_key, run_id=run_id, k=k).invoke(question), None)
        if docs is None:
            return {"error": self.vector_warning or "Vector lookup is turned off"}
        if not docs:
            return {"passages": [], "message": "No indexed documents for this run - attach a requirement document first."}
        out = {"passages": [{"file": d.metadata.get("filename"), "chunk": d.metadata.get("chunk"),
                             "relevance": d.metadata["score"], "text": d.page_content[:1500]} for d in docs]}
        if answer:
            from .lc import answer_chain, default_chat_model, format_docs
            chain = answer_chain(self.rag_llm or default_chat_model())
            out["answer"] = chain.invoke({"context": format_docs(docs), "question": question})
        return out

    def sync_excel(self, file: str, apply: bool = False) -> dict:
        """Compare an edited Excel with the current tasks; with apply=True update them and rebuild the plan."""
        if not self.tasks:
            raise RuntimeError("No tasks yet - analyze a file first, then sync the edited Excel.")
        src = self._resolve(file)
        if src.suffix.lower() not in (".xlsx", ".xlsm"):
            raise ValueError("sync_excel needs an Excel file (.xlsx/.xlsm)")
        new = read_tasks(src, self.config)
        embedder = self.vectors.embedder if self.vectors else None
        try:
            report = diff_tasks(self.tasks, new, embedder)
        except Exception as exc:          # embeddings down: fuzzy title matching still works
            self._vector_failed(exc)
            report = diff_tasks(self.tasks, new, None)
        self.sync_report = report.dump() | {"file": src.name, "applied": False, "has_changes": report.has_changes}
        self.audit.record("excel_sync", reason=f"{src.name}: {self.sync_report['summary']}",
                          data={"apply": apply, "matched_by": report.how})
        if apply and report.has_changes:
            removed = [r["task_id"] for r in report.removed]
            self.tasks = apply_sync(self.tasks, new, report)
            for c in report.changed:
                self.audit.record("excel_sync_change", task=c.title, task_id=c.task_id,
                                  reason=f"{c.field}: '{c.old}' -> '{c.new}'" + (f" (Jira {c.jira_key} will be updated)" if c.jira_key else ""))
            self._vec(lambda v: v.remove_tasks(self.run_id, removed))
            self._rebuild()
            self.sync_report["applied"] = True
            return self.sync_report | {"plan": self.show_plan("summary")}
        return self.sync_report

    def search_jira(self, text: str) -> dict:
        if self.offline:
            return {"error": "Offline mode - Jira is not queried."}
        from .jira_mcp import McpJira

        async def run():
            async with McpJira(self.config) as jira:
                safe = text.replace('"', " ").replace("\\", " ")
                return await jira.search(f'project = {self.project_key} AND text ~ "{safe}"', 20)
        issues = asyncio.run(run())
        return {"issues": [{"key": i.key, "summary": i.summary, "type": i.issue_type, "parent": i.epic_key} for i in issues]}

    def fill_excel_product(self, file: str) -> dict:
        src = self._resolve(file)
        dst = self.run_dir / f"{src.stem}.classified{src.suffix}"
        stats = update_product_column(src, dst, self.config, self.assessor, self.audit)
        self.downloads[dst.name] = dst
        return stats | {"output_file": dst.name}

    def speak_plan(self) -> dict:
        """The plan summary as a wav file (Hugging Face text-to-speech), offered in the downloads."""
        from . import speech
        plan = self._need_plan()
        text = speech.summary_text(plan)
        info = speech.synthesize(text, self.run_dir / "plan_summary.wav")
        self.downloads["plan_summary.wav"] = info["path"]
        return {"text": text, "audio_file": "plan_summary.wav", "seconds": info["seconds"], "model": info["model"]}

    def check_workshop(self, file: str) -> dict:
        src = self._resolve(file)
        target = self.run_dir / "workshop" / src.stem
        if src.suffix.lower() == ".zip":
            with zipfile.ZipFile(src) as z:
                z.extractall(target)
        else:
            raise ValueError("Please attach the submission as a .zip file")
        self.workshop_report = validate_package(target, self.config)
        return self.workshop_report

    def prepare_jira_push(self) -> dict:
        p = self._need_plan()
        self.approval_requested = True
        counts = p["summary"]["counts"]
        return {"message": "Approval panel shown in the sidebar. The user must click Approve, then 'Đẩy lên Jira'.",
                "to_create": counts["to_create"], "to_update": counts.get("to_update", 0), "skipped": counts["skipped"],
                "jira_checked": p["jira_duplicate_check"], "offline": self.offline}

    # -------------------------------------------------------------- after human actions
    def record_approval(self, decision: dict) -> None:
        self.repo.record_approval(self.run_id, decision)

    def after_push(self, state: dict) -> None:
        """Remember the Jira key of every created task so later edits / Excel syncs update instead of re-creating."""
        self.push_state = state
        if not self.plan:
            return
        self.repo.save_push_state(self.run_id, plan_hash(self.plan), state)
        ref_task = {n["ref"]: n["task_id"] for n in self.plan["planned_issues"] if n["task_id"]}
        keys = {ref_task[ref]: key for ref, key in state.get("created", {}).items() if ref in ref_task}
        updated = {n["task_id"] for n in self.plan["planned_issues"] if n.get("update_fields") and n["task_id"]}
        for t in self.tasks:
            if t.id in keys:
                t.jira_key = keys[t.id]
            if t.id in keys or t.id in updated:
                t.pending_update = []
        keys.update({t.id: t.jira_key for t in self.tasks if t.id in updated and t.jira_key})
        if keys:
            self.repo.set_jira_keys(self.run_id, keys)
            hpd = self.config["estimation"].get("hours_per_day", 8)
            self._vec(lambda v: v.index_tasks(self.project_key, self.run_id, self.tasks, hpd))

    # -------------------------------------------------------------- state for the prompt
    def state(self) -> dict:
        s = {"run_id": self.run_id[:8], "attached_files": sorted(self.files), "offline_mode": self.offline,
             "rules_profile": self.project or "default", "jira_project": self.project_key, "has_plan": bool(self.plan),
             "downloads": sorted(self.downloads), "last_error": self.last_error}
        if self.vector_warning:
            s["vector_lookup"] = self.vector_warning
        if self._jira_toolbox:
            s["jira_tools"] = {"policy": self._jira_toolbox.policy, "count": len(self._jira_toolbox.tools),
                               "pending_actions": [{"id": a["id"], "tool": a["tool"]} for a in self._jira_toolbox.pending()]}
        elif self.jira_warning:
            s["jira_tools"] = self.jira_warning
        if self.plan:
            s["plan_version"] = self.plan.get("version")
            s["plan_summary"] = self.plan["summary"]["lines"]
        if self.sync_report:
            s["last_excel_sync"] = {k: self.sync_report[k] for k in ("file", "summary", "applied")}
        if self.push_state:
            s["push_result"] = {"created": self.push_state.get("created"), "skipped": self.push_state.get("skipped"),
                                "updated": len(self.push_state.get("updated", []))}
        return s


def _client():
    return get_client()


def run_turn(messages: list[dict], session: AgentSession, on_tool: Callable[[str, dict, dict], None] | None = None,
             client=None, confirm: Callable[[list[dict]], bool] | None = None) -> str:
    """Run one user turn on the LangGraph agent (graph.py): call the model, execute requested tools, repeat until it
    answers.

    `messages` is the OpenAI chat history (without the system prompt); it is extended in place with the
    assistant/tool messages so later turns keep the context. With `confirm`, the graph stops before every tool step and
    `confirm(tool_calls)` decides whether they run (human in the loop).
    """
    from .graph import AgentGraph
    graph = AgentGraph(session, client=client or _client(), interrupt=confirm is not None, on_tool=on_tool)
    result = graph.run(messages)
    while result.paused:
        result = graph.resume(approve=bool(confirm(result.paused)))
    return result.text


def suggest_prompts(session: AgentSession) -> list[str]:
    """Next-step suggestions derived from the session state."""
    if not session.plan:
        if session.files:
            names = ", ".join(sorted(session.files)[:2])
            return [f"Phân tích {names}", f"Phân tích {names} và gắn vào Epic KAN-1",
                    "Điền cột Product cho file Excel này", "Các file này cần định dạng thế nào?"]
        return ["Phân tích file mẫu task_review.xlsx", "Phân tích tài liệu mẫu brd_customer_portal.md",
                "Xem các phiên làm việc trước", "Ứng dụng này làm được gì?"]
    p = session.plan
    findings, issues = p["estimate_findings"], p["validation_issues"]
    out = []
    if session.sync_report and session.sync_report["has_changes"] and not session.sync_report["applied"]:
        out.append(f"Áp dụng thay đổi từ {session.sync_report['file']}")
    if any(f["rule"] == "missing_estimate" for f in findings):
        out.append("Áp dụng estimate gợi ý cho các task thiếu estimate")
    if any(i["kind"] == "Possible Duplicate" for i in issues):
        out.append("Các task nghi trùng là gì? Có nên bỏ không?")
    if any(p.endswith((".md", ".docx", ".pdf", ".txt")) for p in session.files):
        out.append("Tài liệu yêu cầu nói gì về thanh toán?")
    out.append("Trước đây đã có task nào tương tự chưa?")
    if session.push_state:
        out.append("Tóm tắt những gì đã tạo trên Jira")
    else:
        out.append("Tôi muốn duyệt và đẩy lên Jira")
    return out[:5]
