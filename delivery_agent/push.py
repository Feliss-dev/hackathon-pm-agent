"""Step 7 - push an approved plan to Jira through MCP.

Progress is checkpointed to <plan>.result.json after every created issue, so a failed run can be
re-run without creating the same issues twice.
"""
from __future__ import annotations

import json
from pathlib import Path

from .audit import AuditLog
from .models import PlannedIssue


def result_path(plan_path: Path) -> Path:
    return plan_path.with_name(plan_path.stem + ".result.json")


async def push_plan(plan: dict, plan_path: Path, config: dict, audit: AuditLog, jira) -> dict:
    """`jira` is an entered McpJira (or any object with create_issue / link)."""
    rpath = result_path(plan_path)
    state = json.loads(rpath.read_text(encoding="utf-8")) if rpath.exists() else {"created": {}, "linked": [], "skipped": {}}
    created: dict[str, str] = state["created"]
    nodes = [PlannedIssue(**n) for n in plan["planned_issues"]]
    by_ref = {n.ref: n for n in nodes}
    keys: dict[str, str] = {n.ref: n.existing_key for n in nodes if n.existing_key}
    keys.update(created)

    def save():
        rpath.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def skipped(n: PlannedIssue) -> str:
        if n.skip_reason:
            return n.skip_reason
        if n.parent_ref and n.parent_ref in by_ref and (r := skipped(by_ref[n.parent_ref])):
            return f"parent {n.parent_ref} not created ({r})"
        return ""

    # Create parents before children: subtasks may reference tasks listed after them.
    def depth(n: PlannedIssue) -> int:
        return 0 if not n.parent_ref or n.parent_ref not in by_ref else 1 + depth(by_ref[n.parent_ref])

    for n in sorted(nodes, key=depth):
        if n.ref in keys:
            continue
        if reason := skipped(n):
            state["skipped"][n.ref] = reason
            audit.record("push_skip", task=n.summary, task_id=n.task_id, jiraType=n.issue_type, reason=reason)
            continue
        parent_key = keys.get(n.parent_ref, "") if n.parent_ref else n.parent_key
        try:
            key = await jira.create_issue(n, parent_key)
        except Exception as exc:
            # Names from Excel/docs often are not Jira accounts: create unassigned instead of stopping.
            if not n.assignee or not any(w in str(exc).lower() for w in ("assignee", "user", "account")):
                raise
            audit.record("push_unassigned", task=n.summary, task_id=n.task_id,
                         reason=f"Assignee '{n.assignee}' not accepted by Jira ({exc}); creating unassigned")
            key = await jira.create_issue(n.model_copy(update={"assignee": ""}), parent_key)
        keys[n.ref] = created[n.ref] = key
        save()
        audit.record("push_create", task=n.summary, task_id=n.task_id, jiraType=n.issue_type,
                     parent=parent_key or "(none)", estimate=f"{n.estimate_hours:g}h" if n.estimate_hours else "",
                     reason=f"Created {key} as {n.type_name}",
                     data={"key": key, "labels": n.labels, "components": n.components, "sprint": n.sprint_id})

    # Issues that already exist in Jira but changed in an Excel sync: update them (never delete).
    updated: list[str] = state.setdefault("updated", [])
    for n in nodes:
        if not (n.existing_key and n.update_fields) or n.skip_reason:
            continue
        tag = f"{n.existing_key}:{','.join(sorted(n.update_fields))}:{n.summary}:{n.estimate_hours}:{n.assignee}"
        if tag in updated:
            continue
        await jira.update_issue(n.existing_key, n, n.update_fields)
        updated.append(tag)
        save()
        audit.record("push_update", task=n.summary, task_id=n.task_id, jiraType=n.issue_type,
                     reason=f"Updated {n.existing_key}: {', '.join(n.update_fields)}",
                     data={"key": n.existing_key, "fields": n.update_fields})

    jcfg = config["jira"]
    links: list[tuple[str, str, str]] = []
    if jcfg.get("link_dependencies", True):
        links += [(dep, n.ref, jcfg.get("dependency_link_type", "Blocks")) for n in nodes for dep in n.depends_on_refs]
    links += [(story, task, jcfg.get("story_link_type", "Relates")) for task, story in plan.get("story_links", [])]
    for src, dst, link_type in links:
        tag = f"{src}->{dst}:{link_type}"
        if tag in state["linked"] or src not in keys or dst not in keys:
            continue
        await jira.link(keys[src], keys[dst], link_type)
        state["linked"].append(tag)
        save()
        audit.record("push_link", reason=f"{keys[src]} {link_type} {keys[dst]}",
                     data={"from": keys[src], "to": keys[dst], "type": link_type})

    save()
    return state


async def push_with_mcp(plan: dict, plan_path: Path, config: dict, audit: AuditLog) -> dict:
    from .jira_mcp import McpJira
    async with McpJira(config) as jira:
        return await push_plan(plan, plan_path, config, audit, jira)
