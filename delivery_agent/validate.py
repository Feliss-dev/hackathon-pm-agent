"""Step 4 - required-field and duplicate validation."""
from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from .estimate import parse_hours
from .models import CATEGORIES, Task, ValidationIssue

_FIELD_ATTR = {"title": "title", "assignee": "assignee", "estimate": "estimate", "category": "category",
               "description": "description", "acceptanceCriteria": "acceptance_criteria"}


@dataclass
class ExistingIssue:
    key: str
    summary: str
    issue_type: str = ""
    epic_key: str = ""     # parent epic, if known


def normalise(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (title or "").lower()).strip()


def similarity(a: str, b: str) -> float:
    return fuzz.token_sort_ratio(normalise(a), normalise(b))


class Validator:
    def __init__(self, config: dict):
        self.required = config["validation"]["required_fields"]
        self.blocking = set(config["validation"].get("blocking_fields", self.required))
        self.threshold = config["validation"].get("duplicate_threshold", 85)
        self.semantic_threshold = config["validation"].get("semantic_threshold", 0.88)
        self.hpd = config["estimation"].get("hours_per_day", 8)

    def required_fields(self, task: Task) -> list[ValidationIssue]:
        issues = []
        for field in self.required:
            if not str(getattr(task, _FIELD_ATTR.get(field, field), "") or "").strip():
                blocking = field in self.blocking
                detail = f"Required field '{field}' is empty"
                if field == "assignee" and not blocking:
                    detail += " - will be created unassigned"
                issues.append(ValidationIssue(task_id=task.id, task=task.title or task.id, kind="Missing Field",
                                              severity="High" if blocking else "Medium", detail=detail))
        if task.category and task.category not in CATEGORIES:
            issues.append(ValidationIssue(task_id=task.id, task=task.title, kind="Invalid Value", severity="High",
                                          detail=f"Unknown category '{task.category}'"))
        if task.estimate and parse_hours(task.estimate, self.hpd) is None:
            issues.append(ValidationIssue(task_id=task.id, task=task.title, kind="Invalid Value", severity="High",
                                          detail=f"Estimate '{task.estimate}' cannot be parsed"))
        return issues

    def duplicates(self, tasks: list[Task], existing: list[ExistingIssue], epic_key: str = "",
                   history: dict[str, list[dict]] | None = None) -> list[ValidationIssue]:
        """`history`: task id -> nearest tasks of earlier runs from the vector store
        ({title, score, jira_key, run_id, task_id}); a match by meaning, not by wording."""
        issues = []
        # Within the batch
        for i, a in enumerate(tasks):
            for b in tasks[i + 1:]:
                score = similarity(a.title, b.title)
                if score >= self.threshold:
                    issues.append(ValidationIssue(
                        task_id=b.id, task=b.title, kind="Possible Duplicate", severity="Medium",
                        detail=f"Similar to task {a.id} in the same batch ({score:.0f}%)",
                        match_key=a.id, match_title=a.title, score=score, scope="batch"))
        # Against Jira; matches inside the target epic are reported with scope "epic"
        for task in tasks:
            best = None
            for issue in existing:
                score = similarity(task.title, issue.summary)
                if score >= self.threshold and (best is None or score > best[0]):
                    best = (score, issue)
            if best:
                score, issue = best
                scope = "epic" if epic_key and issue.epic_key == epic_key else "jira"
                where = f"existing {issue.issue_type or 'issue'} in epic {epic_key}" if scope == "epic" else "existing Jira item"
                issues.append(ValidationIssue(
                    task_id=task.id, task=task.title, kind="Possible Duplicate", severity="High",
                    detail=f"Similar to {where} {issue.key} '{issue.summary}' ({score:.0f}%)",
                    match_key=issue.key, match_title=issue.summary, score=score, scope=scope))
        # Against earlier runs (semantic). Skipped when the same Jira issue was already reported above.
        reported = {(i.task_id, i.match_key) for i in issues}
        for task in tasks:
            best = max((h for h in (history or {}).get(task.id, []) if h["score"] >= self.semantic_threshold),
                       key=lambda h: h["score"], default=None)
            if not best or (best.get("jira_key") and (task.id, best["jira_key"]) in reported):
                continue
            in_jira = bool(best.get("jira_key"))
            where = f"Jira issue {best['jira_key']}" if in_jira else f"task {best['task_id']} of an earlier run"
            issues.append(ValidationIssue(
                task_id=task.id, task=task.title, kind="Possible Duplicate", severity="High" if in_jira else "Low",
                detail=f"Same meaning as {where} '{best['title']}' (semantic {best['score']:.0%})",
                match_key=best.get("jira_key") or f"{best['run_id'][:8]}/{best['task_id']}", match_title=best["title"],
                score=round(best["score"] * 100, 1), scope="history"))
        return issues
