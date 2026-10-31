"""Excel -> task sync: diff, apply, and pushing the changes of tasks already in Jira as updates."""
import asyncio
import json

import openpyxl
from mcp.server import MCPServer

from delivery_agent.jira_mcp import McpJira
from delivery_agent.push import push_plan


def _edit(src, dst):
    """Delete the Backfill row, raise one estimate, add a new task at the end."""
    wb = openpyxl.load_workbook(src)
    ws = wb["Tasks"]
    for r in range(1, ws.max_row + 1):
        if ws.cell(r, 3).value == "Create Customer API":
            ws.cell(r, 6).value = 30
            ws.cell(r, 5).value = "Khoa (BE)"
    backfill = next(r for r in range(1, ws.max_row + 1) if ws.cell(r, 3).value == "Backfill missing customer emails")
    ws.delete_rows(backfill)
    last = max(r for r in range(1, ws.max_row + 1) if ws.cell(r, 3).value)
    ws.cell(last + 1, 2).value = "DevOps"
    ws.cell(last + 1, 3).value = "Set up CI pipeline"
    ws.cell(last + 1, 6).value = 8
    wb.save(dst)


def test_diff_only_reports(make_session, workbook, tmp_path):
    s = make_session()
    s.analyze([str(workbook)])
    edited = tmp_path / "edited.xlsx"
    _edit(workbook, edited)
    report = s.sync_excel(str(edited), apply=False)
    assert [a["title"] for a in report["added"]] == ["Set up CI pipeline"]
    assert [r["title"] for r in report["removed"]] == ["Backfill missing customer emails"]
    changed = {(c["title"], c["field"]): (c["old"], c["new"]) for c in report["changed"]}
    assert changed == {("Create Customer API", "estimate"): ("24", "30"),
                       ("Create Customer API", "assignee"): ("Chi (BE)", "Khoa (BE)")}
    assert report["unchanged"] == 12 and not report["applied"]
    assert s.plan["version"] == 1                                   # nothing applied yet


def test_apply_creates_new_plan_version_and_updates_jira_tasks(make_session, workbook, tmp_path, config):
    s = make_session()
    s.analyze([str(workbook)])
    api = next(t for t in s.tasks if t.title == "Create Customer API")
    api_ref = next(n["ref"] for n in s.plan["planned_issues"] if n["task_id"] == api.id)
    s.after_push({"created": {api_ref: "KAN-7"}, "linked": [], "skipped": {}})   # pretend it was pushed

    edited = tmp_path / "edited.xlsx"
    _edit(workbook, edited)
    result = s.sync_excel(str(edited), apply=True)
    assert result["applied"] and s.plan["version"] == 2
    titles = [t.title for t in s.tasks]
    assert "Set up CI pipeline" in titles and "Backfill missing customer emails" not in titles
    assert len(set(t.id for t in s.tasks)) == len(s.tasks)        # new row id never collides with a stored id

    node = next(n for n in s.plan["planned_issues"] if n["task_id"] == api.id)
    assert node["existing_key"] == "KAN-7" and node["update_fields"] == ["assignee", "estimate"]
    assert node["estimate_hours"] == 30 and not node["skip_reason"]
    assert s.plan["summary"]["counts"]["to_update"] == 1
    assert s.repo.task_versions(s.run_id)[api.id] == 2

    # Push: the existing issue is updated, not created again
    store = {"created": [], "updated": []}
    server = MCPServer("fake-jira")

    @server.tool()
    def jira_search(jql: str, fields: str = "", limit: int = 50, start_at: int = 0) -> str:
        return json.dumps({"issues": []})

    @server.tool()
    def jira_create_issue(project_key: str, summary: str, issue_type: str, description: str = "",
                          assignee: str = "", components: str = "", additional_fields: str = "") -> str:
        store["created"].append(summary)
        return json.dumps({"issue": {"key": f"KAN-{100 + len(store['created'])}"}})

    @server.tool()
    def jira_create_issue_link(link_type: str, inward_issue_key: str, outward_issue_key: str) -> str:
        return "{}"

    @server.tool()
    def jira_update_issue(issue_key: str, fields: str, additional_fields: str = "") -> str:
        store["updated"].append((issue_key, json.loads(fields), json.loads(additional_fields or "{}")))
        return "{}"

    async def push():
        async with McpJira(config, server=server) as jira:
            return await push_plan(s.plan, s.plan_path, config, s.audit, jira)

    state = asyncio.run(push())
    assert "Create Customer API" not in store["created"]
    assert store["updated"] == [("KAN-7", {"assignee": "Khoa (BE)"},
                                 {"timetracking": {"originalEstimate": "30h"}})]
    s.after_push(state)
    assert next(t for t in s.tasks if t.id == api.id).pending_update == []
    asyncio.run(push())                                           # re-run: no second update
    assert len(store["updated"]) == 1
