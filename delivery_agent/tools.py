"""Function-calling tools for the chat agent - one registry, typed arguments.

Each tool is declared once with a Pydantic model for its arguments. The JSON schema sent to the model is generated
from that model, and the model's arguments are validated against it before the tool runs, so the schema and the
code cannot drift apart. A bad call (unknown tool, wrong type, missing field) goes back to the model as an
`{"error": ...}` result it can correct. Every call is timed and stored (audit log + `tool_calls` table).

Plan approval and push are buttons a person clicks. The Jira MCP tools (jira_tools.py) can read freely; their writes
are only proposed and run after a person approves them.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, ValidationError

if TYPE_CHECKING:
    from .agent import AgentSession


@dataclass
class Tool:
    name: str
    description: str
    args: type[BaseModel]
    fn: Callable[["AgentSession", BaseModel], dict]
    label: str

    def schema(self) -> dict:
        params = _inline(self.args.model_json_schema())
        params.setdefault("properties", {})
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": params}}


def _inline(schema: dict) -> dict:
    """Plain JSON schema for function calling: `$ref`s resolved (not every gateway supports them), titles dropped."""
    defs = schema.get("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].split("/")[-1]])
            # a "title" whose value is a string is schema metadata; a property called "title" is a dict
            return {k: walk(v) for k, v in node.items() if k != "$defs" and not (k == "title" and isinstance(v, str))}
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node
    return walk(schema)


class ToolRegistry:
    def __init__(self):
        self.tools: dict[str, Tool] = {}

    def register(self, name: str, description: str, args: type[BaseModel], label: str):
        def deco(fn):
            self.tools[name] = Tool(name, description, args, fn, label)
            return fn
        return deco

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self.tools.values()]

    def labels(self) -> dict[str, str]:
        return {n: t.label for n, t in self.tools.items()}

    def call(self, session: "AgentSession", name: str, raw_args: str | dict | None) -> tuple[dict, dict]:
        """Validate and run one tool call. Returns (args as parsed, result). Never raises."""
        started = time.perf_counter()
        args: dict = {}
        try:
            args = json.loads(raw_args or "{}") if isinstance(raw_args, str) else dict(raw_args or {})
            tool = self.tools.get(name)
            jira = session.jira_toolbox if tool is None and name.startswith("jira_") else None
            if jira is not None and name in jira.names:      # a tool of the Jira MCP server (see jira_tools.py)
                result = jira.call(name, args)
            elif tool is None:
                raise ValueError(f"Unknown tool '{name}'. Available: {', '.join(self.tools)}")
            else:
                parsed = tool.args.model_validate(args)
                result = tool.fn(session, parsed)
            session.last_error = ""
        except ValidationError as exc:
            result = {"error": f"Invalid arguments for {name}: "
                               + "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())}
        except Exception as exc:  # reported to the model so it can recover or explain
            result = {"error": f"{type(exc).__name__}: {exc}"}
        if "error" in result:
            session.last_error = result["error"]
        session.record_tool_call(name, args, result, int((time.perf_counter() - started) * 1000))
        return args, result


REGISTRY = ToolRegistry()
tool = REGISTRY.register


# ------------------------------------------------------------------ argument models
class NoArgs(BaseModel):
    pass


class AnalyzeArgs(BaseModel):
    files: list[str] = Field(description="File names from list_files")
    epic_key: str = Field("", description="Existing Epic key, e.g. KAN-1")
    new_epic: str = Field("", description="Summary of a new Epic to create instead")
    sprint_id: int | None = Field(None, description="Jira sprint id (Scrum boards only)")
    include_duplicates: bool = False
    allow_incomplete: bool = False


class ShowPlanArgs(BaseModel):
    section: Literal["summary", "tree", "tasks", "estimate_warnings", "validation_issues", "skipped"]


class TaskUpdate(BaseModel):
    task_id: str
    field: Literal["title", "description", "assignee", "estimate", "story"]
    value: str


class UpdateTasksArgs(BaseModel):
    updates: list[TaskUpdate]


class ApplyEstimatesArgs(BaseModel):
    only_missing: bool = Field(True, description="true: only tasks with no estimate")
    task_ids: list[str] = Field(default_factory=list, description="Limit to these tasks")


class AssessTaskArgs(BaseModel):
    title: str
    description: str = ""
    assignee: str = ""
    estimate: str = ""


class TextArgs(BaseModel):
    text: str


class FileArgs(BaseModel):
    file: str


class SimilarTasksArgs(BaseModel):
    text: str = Field(description="Task title/description to look up")
    k: int = Field(5, ge=1, le=20)


class AskRequirementsArgs(BaseModel):
    question: str = Field(description="Question about the uploaded requirement documents")
    k: int = Field(5, ge=1, le=10)
    all_runs: bool = Field(False, description="true: search documents of every earlier run of this project too")
    answer: bool = Field(False, description="true: also return a grounded answer with [file#chunk] citations (extra model call)")


class SyncExcelArgs(BaseModel):
    file: str = Field(description="Edited Excel task list (name from list_files)")
    apply: bool = Field(False, description="false: only show the differences; true: update tasks and rebuild the plan")


class RunIdArgs(BaseModel):
    run_id: str = Field(description="Run id (or its first 8 characters) from list_runs")


# ------------------------------------------------------------------ tools
@tool("list_files", "List files attached by the user and the built-in sample files.", NoArgs, "Xem danh sách file")
def _list_files(s, a):
    return s.list_files()


@tool("analyze", "Run the full analysis on files: extract tasks (AI for documents, direct read for Excel), classify, "
                 "review estimates, validate (required fields, duplicates incl. Jira and earlier runs by meaning) and "
                 "build the Jira hierarchy.", AnalyzeArgs, "Phân tích & lập kế hoạch")
def _analyze(s, a: AnalyzeArgs):
    return s.analyze(**a.model_dump())


@tool("show_plan", "Get details of the current plan.", ShowPlanArgs, "Xem chi tiết plan")
def _show_plan(s, a: ShowPlanArgs):
    return s.show_plan(a.section)


@tool("update_tasks", "Edit fields of tasks in the current plan, then rebuild the plan.", UpdateTasksArgs, "Sửa task")
def _update_tasks(s, a: UpdateTasksArgs):
    return s.update_tasks([u.model_dump() for u in a.updates])


@tool("apply_suggested_estimates", "Set estimates to the suggested values from the estimate review, then rebuild the plan.",
      ApplyEstimatesArgs, "Áp estimate gợi ý")
def _apply_estimates(s, a: ApplyEstimatesArgs):
    return s.apply_suggested_estimates(a.only_missing, a.task_ids or None)


@tool("assess_task", "Apply the project's rules to one task: category with the rule that decided it, and the estimate "
                     "limit / suggestion. Use it to answer 'what category would X be?'.", AssessTaskArgs, "Áp rule cho task")
def _assess_task(s, a: AssessTaskArgs):
    return s.assess_task(**a.model_dump())


@tool("search_jira", "Read-only search of existing issues in the Jira project by text.", TextArgs, "Tìm trên Jira")
def _search_jira(s, a: TextArgs):
    return s.search_jira(a.text)


@tool("search_similar_tasks", "Semantic search (embeddings) over tasks of earlier plans of this project: similar work, "
                              "its category and final estimate. Use for 'have we done something like X?' or to justify "
                              "an estimate.", SimilarTasksArgs, "Tìm task tương tự")
def _similar(s, a: SimilarTasksArgs):
    return s.search_similar_tasks(a.text, a.k)


@tool("ask_requirements", "Semantic search over the uploaded requirement documents. Returns the most relevant passages "
                          "with file name; answer ONLY from them and cite the file.", AskRequirementsArgs,
      "Tra cứu tài liệu yêu cầu")
def _ask(s, a: AskRequirementsArgs):
    return s.ask_requirements(a.question, a.k, a.all_runs, a.answer)


@tool("sync_excel", "Compare an edited Excel task list with the current tasks (added / changed / removed). With "
                    "apply=true update the tasks and rebuild the plan; tasks already in Jira will be updated, not "
                    "created again. Show the differences first and apply only when the user agrees.",
      SyncExcelArgs, "Đồng bộ từ Excel")
def _sync(s, a: SyncExcelArgs):
    return s.sync_excel(a.file, a.apply)


@tool("fill_excel_product", "Fill the Product column of a task tracking workbook with the classification (new file).",
      FileArgs, "Điền cột Product")
def _fill(s, a: FileArgs):
    return s.fill_excel_product(a.file)


@tool("read_plan_aloud", "Read the summary of the current plan aloud with a Hugging Face text-to-speech model; the audio "
                         "file is offered for download.", NoArgs, "Đọc to tóm tắt plan")
def _speak(s, a):
    return s.speak_plan()


@tool("check_workshop", "Check a workshop submission (.zip) for required artifacts and give a readiness score.",
      FileArgs, "Kiểm tra workshop")
def _workshop(s, a: FileArgs):
    return s.check_workshop(a.file)


@tool("recheck_with_jira", "Turn off offline mode and re-check the current plan for duplicates against Jira (read-only). "
                           "Needed before pushing a plan that was built offline.", NoArgs, "Kiểm tra trùng với Jira")
def _recheck(s, a):
    return s.recheck_with_jira()


@tool("list_runs", "List earlier planning sessions (runs) of this project stored in the database.", NoArgs,
      "Xem lịch sử phiên")
def _runs(s, a):
    return {"runs": s.repo.list_runs(s.config["jira"]["project_key"])}


@tool("open_run", "Open an earlier run (its files, tasks and latest plan) from the database.", RunIdArgs,
      "Mở lại phiên cũ")
def _open_run(s, a: RunIdArgs):
    return s.open_run(a.run_id)


@tool("prepare_jira_push", "Show the approval panel for the current plan. Does NOT push; a human must approve and push.",
      NoArgs, "Mở bảng duyệt Jira")
def _prepare(s, a):
    return s.prepare_jira_push()
