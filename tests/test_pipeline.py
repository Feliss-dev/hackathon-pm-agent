"""End-to-end: plan -> approval gate -> push through a real MCP client to an in-process fake Jira server."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from mcp.server import MCPServer

from delivery_agent.approval import ApprovalError, prompt_decision, record_decision, require_approval
from delivery_agent.audit import AuditLog
from delivery_agent.excel_io import read_tasks
from delivery_agent.extract import extract_from_documents
from delivery_agent.jira_mcp import McpJira
from delivery_agent.planner import build_plan, render_markdown
from delivery_agent.push import push_plan


def fake_jira_server(store: dict, fail_on: str = "") -> MCPServer:
    server = MCPServer("fake-jira")

    @server.tool()
    def jira_search(jql: str, fields: str = "", limit: int = 50, start_at: int = 0) -> str:
        issues = [{"key": k, "summary": v["summary"], "issue_type": {"name": v["issue_type"]},
                   "parent": {"key": v["parent"]} if v["parent"] else None} for k, v in store["issues"].items()]
        if jql.startswith("parent = "):
            issues = [i for i in issues if i["parent"] and i["parent"]["key"] == jql.split("= ")[1]]
        return json.dumps({"issues": issues[start_at:start_at + limit]})

    @server.tool()
    def jira_create_issue(project_key: str, summary: str, issue_type: str, description: str = "",
                          assignee: str = "", components: str = "", additional_fields: str = "") -> str:
        if fail_on and summary == fail_on:
            raise ValueError("Jira rejected the issue")
        key = f"{project_key}-{100 + len(store['issues'])}"
        extra = json.loads(additional_fields) if additional_fields else {}   # like mcp-atlassian: a JSON string
        store["issues"][key] = {"summary": summary, "issue_type": issue_type, "parent": extra.get("parent", ""),
                                "assignee": assignee, "components": components, "extra": extra}
        return json.dumps({"message": "Issue created", "issue": {"key": key}})

    @server.tool()
    def jira_create_issue_link(link_type: str, inward_issue_key: str, outward_issue_key: str) -> str:
        store["links"].append((inward_issue_key, link_type, outward_issue_key))
        return json.dumps({"success": True})

    return server


def _store():
    return {"issues": {"DT-1": {"summary": "Customer Portal", "issue_type": "Epic", "parent": ""},
                       "DT-2": {"summary": "Customer Onboarding", "issue_type": "Story", "parent": "DT-1"},
                       "DT-3": {"summary": "Create Customer API", "issue_type": "Task", "parent": "DT-1"}},
            "links": []}


async def _existing(config, store):
    async with McpJira(config, server=fake_jira_server(store)) as jira:
        return await jira.search("project = DT")


async def _push(plan, plan_path, config, audit, store, fail_on=""):
    async with McpJira(config, server=fake_jira_server(store, fail_on)) as jira:
        return await push_plan(plan, plan_path, config, audit, jira)


@pytest.fixture
def planned(config, workbook, tmp_path, assessor):
    config["jira"]["category_mapping"]["FE"]["components"] = ["Frontend"]  # company-managed style
    store = _store()
    existing = asyncio.run(_existing(config, store))
    audit = AuditLog(tmp_path / "audit.jsonl")
    plan = build_plan(read_tasks(workbook, config), config, audit, assessor=assessor, existing=existing,
                      epic_key="DT-1", sprint_id=7)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    return SimpleNamespace(plan=plan, path=plan_path, audit=audit, store=store)


def test_plan_summary_and_hierarchy(planned):
    counts = planned.plan["summary"]["counts"]
    assert counts["tasks_extracted"] == 14
    assert counts["possible_duplicates"] >= 2          # vs DT-3 and within batch
    assert counts["estimation_warnings"] >= 5
    nodes = {n["summary"]: n for n in planned.plan["planned_issues"]}
    assert nodes["Customer Onboarding"]["existing_key"] == "DT-2"     # existing story reused
    api = nodes["Create Customer API"]
    assert api["skip_reason"].startswith("Possible Duplicate of DT-3")
    assert nodes["Build customer registration screen"]["type_name"] == "Frontend Task"
    assert "Frontend" in nodes["Build customer registration screen"]["components"]
    assert nodes["Migrate legacy customer data"]["skip_reason"].startswith("Missing Field")
    md = render_markdown(planned.plan)
    assert "Epic DT-1 (existing)" in md and "Story: Customer Onboarding" in md and "[DT-2]" in md


def test_push_requires_approval(planned):
    with pytest.raises(ApprovalError, match="No approval"):
        require_approval(planned.path, planned.plan)
    record_decision(planned.path, planned.plan, "pm", False, planned.audit)
    with pytest.raises(ApprovalError, match="rejected"):
        require_approval(planned.path, planned.plan)
    record_decision(planned.path, planned.plan, "pm", True, planned.audit)
    require_approval(planned.path, planned.plan)
    planned.plan["sprint_id"] = 99                     # tampering after approval
    with pytest.raises(ApprovalError, match="changed"):
        require_approval(planned.path, planned.plan)


def test_prompt_decision():
    answers = iter(["maybe", "A"])
    out = SimpleNamespace(write=lambda s: None)
    assert prompt_decision(["30 tasks"], input_fn=lambda _: next(answers), out=out) is True
    assert prompt_decision(["30 tasks"], input_fn=lambda _: "reject", out=out) is False


def test_push_creates_hierarchy_via_mcp(planned, config):
    state = asyncio.run(_push(planned.plan, planned.path, config, planned.audit, planned.store))
    issues = planned.store["issues"]
    created = {issues[k]["summary"]: issues[k] for k in state["created"].values()}
    screen = created["Build customer registration screen"]
    assert screen["parent"] == "DT-1" and screen["components"] == "Frontend"
    assert screen["extra"]["customfield_10020"] == 7
    assert screen["extra"]["timetracking"] == {"originalEstimate": "32h"}
    assert {"ai-planned", "frontend"} <= set(screen["extra"]["labels"])
    assert "Create Customer API" not in created and "Migrate legacy customer data" not in created
    assert any(t == "Relates" for _, t, _ in planned.store["links"])   # task -> story link
    assert any(t == "Blocks" for _, t, _ in planned.store["links"])                 # dependency link
    steps = [e.step for e in planned.audit.entries]
    assert "push_create" in steps and "push_skip" in steps


def test_push_resumes_after_failure(planned, config):
    with pytest.raises(Exception):
        asyncio.run(_push(planned.plan, planned.path, config, planned.audit, planned.store,
                          fail_on="Create test cases for onboarding"))
    first = dict(planned.store["issues"])
    state = asyncio.run(_push(planned.plan, planned.path, config, planned.audit, planned.store))
    summaries = [v["summary"] for v in planned.store["issues"].values()]
    assert len(summaries) == len(set(summaries))       # nothing created twice
    assert len(planned.store["issues"]) > len(first) and state["created"]


def test_subtask_parent(config, tmp_path, assessor):
    from delivery_agent.models import Task
    tasks = [Task(id="T1", title="Create Customer API", assignee="a", estimate="8h", story="S"),
             Task(id="T2", title="Add input validation", assignee="a", estimate="2h", parentTitle="Create Customer API")]
    plan = build_plan(tasks, config, AuditLog(), assessor=assessor, new_epic="Portal", jira_checked=False)
    nodes = {n["summary"]: n for n in plan["planned_issues"]}
    assert nodes["Portal"]["issue_type"] == "Epic"
    assert nodes["Create Customer API"]["parent_ref"] == "EPIC"
    assert nodes["Add input validation"]["issue_type"] == "Subtask"
    assert nodes["Add input validation"]["parent_ref"] == nodes["Create Customer API"]["ref"]
    assert any("Jira was not queried" in line for line in plan["summary"]["lines"])


def test_claude_extraction_uses_structured_output(tmp_path):
    doc = tmp_path / "brd.md"
    doc.write_text("Build a customer API.", encoding="utf-8")
    payload = {"tasks": [{"title": "Create Customer API", "description": "d", "acceptanceCriteria": "", "assignee": "",
                          "dependency": [], "category": "", "estimate": "", "story": "API", "parentTitle": ""}]}
    calls = {}

    class Stream:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get_final_message(self):
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(payload))])

    def stream(**kwargs):
        calls.update(kwargs)
        return Stream()

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))
    [task] = extract_from_documents([doc], client=client, provider="anthropic")
    assert task.title == "Create Customer API" and task.story == "API" and task.source == "brd.md"
    assert calls["model"] == "claude-opus-5-5"
    assert calls["output_config"]["format"]["type"] == "json_schema"
    assert calls["messages"][0]["content"][0]["source"]["data"] == "Build a customer API."


def test_gateway_extraction_uses_json_schema(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "DeepSeek-V4.1-Flash")
    doc = tmp_path / "brd.md"
    doc.write_text("Build a customer API.", encoding="utf-8")
    payload = {"tasks": [{"title": "Create Customer API", "description": "d", "acceptanceCriteria": "", "assignee": "",
                          "dependency": [], "category": "", "estimate": "", "story": "", "parentTitle": ""}]}
    calls = {}

    def create(**kwargs):
        calls.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)), finish_reason="stop")])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    [task] = extract_from_documents([doc], client=client, provider="openai")
    assert task.title == "Create Customer API"
    assert calls["model"] == "DeepSeek-V4.1-Flash"
    assert calls["response_format"]["type"] == "json_schema" and calls["response_format"]["json_schema"]["strict"] is True
    assert "Build a customer API." in calls["messages"][1]["content"]


def test_unknown_assignee_is_created_unassigned(planned, config):
    class StrictJira:
        def __init__(self): self.created = []
        async def create_issue(self, issue, parent_key=""):
            if issue.assignee:
                raise RuntimeError(f"jira_create_issue failed: Could not find user '{issue.assignee}'")
            self.created.append(issue.summary)
            return f"KAN-{len(self.created)}"
        async def link(self, *a): pass

    jira = StrictJira()
    asyncio.run(push_plan(planned.plan, planned.path, config, planned.audit, jira))
    assert "Build customer registration screen" in jira.created
    assert any(e.step == "push_unassigned" for e in planned.audit.entries)
