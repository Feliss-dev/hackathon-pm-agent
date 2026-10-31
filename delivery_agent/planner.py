"""Steps 2-5: apply the rules (AI), review estimates, validate (code), build the Jira hierarchy and summary."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .audit import AuditLog
from .estimate import parse_hours
from .models import EstimateFinding, PlannedIssue, Task, ValidationIssue
from .validate import ExistingIssue, Validator, normalise, similarity


def _resolve(ref: str, tasks: list[Task], threshold: float) -> Task | None:
    """Match a dependency / parent reference by id, exact title, then fuzzy title."""
    ref_n = normalise(ref)
    for t in tasks:
        if t.id == ref or normalise(t.title) == ref_n:
            return t
    best = max(tasks, key=lambda t: similarity(ref, t.title), default=None)
    return best if best and similarity(ref, best.title) >= threshold else None


def build_plan(tasks: list[Task], config: dict, audit: AuditLog, *, assessor, existing: list[ExistingIssue] | None = None,
               epic_key: str = "", new_epic: str = "", sprint_id: int | None = None,
               include_duplicates: bool = False, allow_incomplete: bool = False,
               jira_checked: bool = True, history: dict[str, list[dict]] | None = None) -> dict:
    existing = existing or []
    jcfg = config["jira"]
    validator = Validator(config)
    hpd = config["estimation"].get("hours_per_day", 8)

    audit.record("extract", reason=f"{len(tasks)} tasks extracted",
                 data={"sources": sorted({t.source.split(":")[0] for t in tasks})})

    # Step 2 - classification: the AI applies the human-written rules
    assessments = assessor.assess(tasks)
    for t in tasks:
        a = assessments[t.id]
        previous = t.category
        t.category = a.category
        reason = f"Theo rule: {a.category_reason}"
        if previous and previous != t.category:
            reason += f" (giá trị cũ '{previous}')"
        audit.record("classify", task=t.title, task_id=t.id, classification=t.category, reason=reason)

    # Step 3 - estimation review: limits/defaults read from the rules by the AI, compared in code
    findings: list[EstimateFinding] = []
    for t in tasks:
        a = assessments[t.id]
        if not str(t.estimate).strip():
            f = EstimateFinding(task_id=t.id, task=t.title, rule="missing_estimate", severity="High",
                                current_estimate="", suggested_estimate=a.suggested_estimate, reason=a.estimate_reason)
        elif parse_hours(t.estimate, hpd) is None:
            f = EstimateFinding(task_id=t.id, task=t.title, rule="unparseable_estimate", severity="High",
                                current_estimate=str(t.estimate), suggested_estimate="",
                                reason="Estimate format not recognised (use e.g. 6h, 2d, 1w)")
        elif not a.estimate_ok:
            f = EstimateFinding(task_id=t.id, task=t.title, rule="rule_limit", severity="Medium",
                                current_estimate=str(t.estimate), suggested_estimate=a.suggested_estimate,
                                reason=a.estimate_reason)
        else:
            continue
        findings.append(f)
        audit.record("estimate_review", task=t.title, task_id=t.id, estimate=t.estimate, reason=f.reason,
                     data={"rule": f.rule, "severity": f.severity, "suggestedEstimate": f.suggested_estimate})

    # Step 4 - validation
    issues: list[ValidationIssue] = []
    for t in tasks:
        issues.extend(validator.required_fields(t))
    issues.extend(validator.duplicates(tasks, existing, epic_key, history))
    for i in issues:
        audit.record("validation", task=i.task, task_id=i.task_id, reason=i.detail,
                     data={"kind": i.kind, "severity": i.severity, "match": i.match_key, "scope": i.scope})

    # Step 5 - hierarchy
    planned: list[PlannedIssue] = []
    epic_ref = ""
    if new_epic and not epic_key:
        epic_ref = "EPIC"
        planned.append(PlannedIssue(ref="EPIC", issue_type=jcfg["epic_issue_type"], type_name="Epic",
                                    summary=new_epic, labels=list(jcfg.get("common_labels", []))))

    epic_stories = [e for e in existing if epic_key and e.epic_key == epic_key
                    and e.issue_type.lower() == jcfg["story_issue_type"].lower()]
    story_refs: dict[str, str] = {}
    for name in dict.fromkeys(t.story for t in tasks if t.story):
        ref = f"S{len(story_refs) + 1}"
        story_refs[name] = ref
        reuse = next((s for s in epic_stories if similarity(name, s.summary) >= validator.threshold), None)
        planned.append(PlannedIssue(
            ref=ref, issue_type=jcfg["story_issue_type"], type_name="Story", summary=name,
            parent_ref=epic_ref, parent_key=epic_key, epic_key=epic_key, sprint_id=sprint_id,
            labels=list(jcfg.get("common_labels", [])), existing_key=reuse.key if reuse else ""))
        audit.record("hierarchy", task=name, jiraType="Story",
                     parent=epic_key or epic_ref or "(none)",
                     reason=f"Reusing existing Story {reuse.key}" if reuse else "Groups tasks sharing this story")

    blocked: dict[str, str] = {}
    if not allow_incomplete:
        for i in issues:
            if i.kind in ("Missing Field", "Invalid Value") and i.severity == "High":
                blocked.setdefault(i.task_id, f"{i.kind}: {i.detail}")
    if not include_duplicates:
        for i in issues:
            # A semantic match with an earlier run only blocks when that task is already in Jira.
            if i.kind == "Possible Duplicate" and (i.scope != "history" or i.severity == "High"):
                blocked.setdefault(i.task_id, f"Possible Duplicate of {i.match_key}")
    for t in tasks:   # already created in Jira by an earlier push: nothing to create, so nothing to skip
        if t.jira_key:
            blocked.pop(t.id, None)

    threshold = validator.threshold
    task_ref = {t.id: f"T{n}" for n, t in enumerate(tasks, 1)}
    task_parent_mode = jcfg.get("task_parent", "epic")
    for t in tasks:
        mapping = jcfg["category_mapping"].get(t.category) or {
            **jcfg["category_mapping"]["default"], "type_name": f"{t.category} Task",
            "labels": jcfg["category_mapping"]["default"].get("labels", []) + [re.sub(r"\W+", "-", t.category.lower())]}
        parent_task = _resolve(t.parent_title, [x for x in tasks if x.id != t.id], threshold) if t.parent_title else None
        story_ref = story_refs.get(t.story, "")
        if parent_task:
            issue_type, type_name, parent_ref, parent_key = jcfg["subtask_issue_type"], f"{mapping['type_name']} (Subtask)", task_ref[parent_task.id], ""
            why_parent = f"Sub-step of '{parent_task.title}'"
        elif task_parent_mode == "story" and story_ref:
            issue_type, type_name, parent_ref, parent_key = mapping["issue_type"], mapping["type_name"], story_ref, ""
            why_parent = f"Belongs to story '{t.story}'"
        else:
            issue_type, type_name, parent_ref, parent_key = mapping["issue_type"], mapping["type_name"], epic_ref, epic_key
            why_parent = "Attached to the epic" + (f"; linked to story '{t.story}'" if story_ref else "")
        deps = []
        for d in t.dependency:
            target = _resolve(d, [x for x in tasks if x.id != t.id], threshold)
            if target:
                deps.append(task_ref[target.id])
        hours = parse_hours(t.estimate, hpd)
        node = PlannedIssue(
            ref=task_ref[t.id], task_id=t.id, issue_type=issue_type, type_name=type_name, summary=t.title,
            description=_description(t), assignee=t.assignee, estimate_hours=hours,
            labels=list(jcfg.get("common_labels", [])) + list(mapping.get("labels", [])),
            components=list(mapping.get("components", [])), parent_ref=parent_ref, parent_key=parent_key,
            epic_key=epic_key, sprint_id=None if parent_task else sprint_id, depends_on_refs=deps,
            skip_reason=blocked.get(t.id, ""), existing_key=t.jira_key,
            update_fields=list(t.pending_update) if t.jira_key else [])
        planned.append(node)
        reason = f"Category {t.category} -> {type_name}. {why_parent}."
        if node.skip_reason:
            reason += f" Not created: {node.skip_reason}."
        audit.record("hierarchy", task=t.title, task_id=t.id, classification=t.category, estimate=t.estimate,
                     jiraType=issue_type, parent=parent_key or parent_ref or "(none)", reason=reason)

    story_links = [(task_ref[t.id], story_refs[t.story]) for t in tasks
                   if t.story in story_refs and not t.parent_title and task_parent_mode != "story"]

    plan = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project_key": jcfg["project_key"],
        "epic_key": epic_key, "new_epic": new_epic, "sprint_id": sprint_id,
        "jira_duplicate_check": jira_checked,
        "tasks": [t.dump() for t in tasks],
        "assessments": {tid: a.dump() for tid, a in assessments.items()},
        "estimate_findings": [f.model_dump(by_alias=True) for f in findings],
        "validation_issues": [i.model_dump() for i in issues],
        "planned_issues": [p.model_dump() for p in planned],
        "story_links": story_links,
    }
    plan["summary"] = summarise(plan)
    audit.record("summary", reason=plan["summary"]["headline"], data=plan["summary"]["counts"])
    return plan


def _description(t: Task) -> str:
    parts = [t.description]
    if t.acceptance_criteria:
        parts.append(f"Acceptance Criteria:\n{t.acceptance_criteria}")
    if t.source:
        parts.append(f"Source: {t.source}")
    return "\n\n".join(p for p in parts if p)


def summarise(plan: dict) -> dict:
    issues = plan["validation_issues"]
    planned = plan["planned_issues"]
    counts = {
        "tasks_extracted": len(plan["tasks"]),
        "possible_duplicates": sum(1 for i in issues if i["kind"] == "Possible Duplicate"),
        "estimation_warnings": len(plan["estimate_findings"]),
        "missing_or_invalid_fields": sum(1 for i in issues if i["kind"] != "Possible Duplicate"),
        "to_create": sum(1 for p in planned if not p["skip_reason"] and not p["existing_key"]),
        "skipped": sum(1 for p in planned if p["skip_reason"]),
        "reused_existing": sum(1 for p in planned if p["existing_key"]),
        "to_update": sum(1 for p in planned if p.get("update_fields") and not p["skip_reason"]),
        "similar_to_history": sum(1 for i in issues if i.get("scope") == "history"),
    }
    by_category: dict[str, int] = {}
    for t in plan["tasks"]:
        by_category[t["category"]] = by_category.get(t["category"], 0) + 1
    lines = [
        f"{counts['tasks_extracted']} tasks extracted",
        f"{counts['possible_duplicates']} possible duplicates found"
        + (f" ({counts['similar_to_history']} similar to tasks of earlier runs - a warning unless that task is "
           f"already in Jira)" if counts["similar_to_history"] else ""),
        f"{counts['estimation_warnings']} estimation warnings detected",
        f"{counts['missing_or_invalid_fields']} missing/invalid required fields",
        f"{counts['to_create']} Jira issues will be created, {counts['skipped']} skipped, "
        f"{counts['reused_existing']} existing reused",
    ]
    if counts["to_update"]:
        lines.append(f"{counts['to_update']} existing Jira issues will be updated (changed in Excel)")
    if not plan.get("jira_duplicate_check"):
        lines.append("WARNING: Jira was not queried - duplicates were checked within this batch only")
    return {"headline": "; ".join(lines[:3]), "lines": lines, "counts": counts, "by_category": by_category}


def render_markdown(plan: dict) -> str:
    s = plan["summary"]
    out = ["# Delivery Plan Review", "", f"Project: **{plan['project_key']}**  "]
    out.append(f"Epic: **{plan['epic_key'] or plan['new_epic'] or '(none)'}**  ")
    out.append(f"Sprint: **{plan['sprint_id'] or '(none)'}**")
    out += ["", "## Summary", ""] + [f"- {line}" for line in s["lines"]]
    out += ["", "| Category | Tasks |", "|---|---|"] + [f"| {k} | {v} |" for k, v in sorted(s["by_category"].items())]

    out += ["", "## Estimation warnings", ""]
    if plan["estimate_findings"]:
        out += ["| Task | Severity | Current | Suggested | Reason |", "|---|---|---|---|---|"]
        out += [f"| {f['task']} | {f['severity']} | {f['currentEstimate'] or '-'} | {f['suggestedEstimate'] or '-'} | {f['reason']} |"
                for f in plan["estimate_findings"]]
    else:
        out.append("None.")

    out += ["", "## Validation issues", ""]
    if plan["validation_issues"]:
        out += ["| Task | Kind | Severity | Detail |", "|---|---|---|---|"]
        out += [f"| {i['task']} | {i['kind']} | {i['severity']} | {i['detail']} |" for i in plan["validation_issues"]]
    else:
        out.append("None.")

    out += ["", "## Proposed Jira hierarchy", "", "```"]
    out += render_tree(plan)
    out += ["```"]
    return "\n".join(out) + "\n"


def render_tree(plan: dict) -> list[str]:
    nodes = plan["planned_issues"]
    children: dict[str, list[dict]] = {}
    for n in nodes:
        children.setdefault(n["parent_ref"] or n["parent_key"] or "", []).append(n)
    story_of = {t: s for t, s in plan.get("story_links", [])}
    for task_ref, story_ref in story_of.items():
        # display tasks under their story even when Jira parent is the epic
        for bucket in children.values():
            for n in list(bucket):
                if n["ref"] == task_ref:
                    bucket.remove(n)
        children.setdefault(story_ref, []).append(next(n for n in nodes if n["ref"] == task_ref))

    def label(n: dict) -> str:
        tag = f" [{n['existing_key']}]" if n["existing_key"] else ""
        est = f" ({n['estimate_hours']:g}h)" if n.get("estimate_hours") else ""
        skip = f"  << SKIP: {n['skip_reason']}" if n["skip_reason"] else ""
        who = f" @{n['assignee']}" if n["assignee"] else ""
        return f"{n['type_name']}: {n['summary']}{est}{who}{tag}{skip}"

    lines: list[str] = []

    def walk(key: str, prefix: str):
        kids = children.get(key, [])
        for i, n in enumerate(kids):
            last = i == len(kids) - 1
            lines.append(f"{prefix}{'└─ ' if last else '├─ '}{label(n)}")
            walk(n["ref"], prefix + ("   " if last else "│  "))

    if plan["epic_key"]:
        lines.append(f"Epic {plan['epic_key']} (existing)")
        walk(plan["epic_key"], "")
    for n in children.get("", []):
        lines.append(label(n))
        walk(n["ref"], "")
    return lines
