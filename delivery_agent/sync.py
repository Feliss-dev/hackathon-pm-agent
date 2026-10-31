"""Update stored tasks from an edited Excel file (team 3 - ThanhHM17).

1. Read the new workbook with the normal Excel reader.
2. Match each new row to a stored task: real work-item ID (#123) -> same normalised title -> same meaning
   (embedding cosine, falls back to fuzzy title match) -> otherwise it is NEW. Stored tasks nobody matched are REMOVED.
3. Compare the fields people edit in Excel and report added / changed / removed / unchanged.
4. `apply_sync` returns the new task list: stored ids and Jira keys are kept, so tasks already in Jira are UPDATED
   (their changed fields go to `pending_update`), never created twice. Removed tasks are only dropped from the plan;
   nothing is deleted in Jira.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from .models import Task
from .validate import normalise, similarity

SYNC_FIELDS = ["title", "description", "acceptance_criteria", "assignee", "estimate", "story", "dependency", "parent_title"]
FIELD_LABEL = {"acceptance_criteria": "acceptanceCriteria", "parent_title": "parentTitle"}
SEMANTIC_MATCH = 0.90
FUZZY_MATCH = 85


@dataclass
class FieldChange:
    task_id: str
    title: str
    field: str
    old: str
    new: str
    jira_key: str = ""


@dataclass
class SyncReport:
    added: list[dict] = field(default_factory=list)       # {excel_id, title}
    changed: list[FieldChange] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)     # {task_id, title, jira_key}
    unchanged: int = 0
    matches: dict[str, str] = field(default_factory=dict)  # excel id -> stored task id
    how: dict[str, str] = field(default_factory=dict)      # excel id -> id | title | semantic | fuzzy

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.changed or self.removed)

    def dump(self) -> dict:
        return {"added": self.added, "changed": [asdict(c) for c in self.changed], "removed": self.removed,
                "unchanged": self.unchanged, "matched_by": self.how,
                "summary": f"{len(self.added)} added, {len({c.task_id for c in self.changed})} changed "
                           f"({len(self.changed)} fields), {len(self.removed)} removed, {self.unchanged} unchanged"}


def _value(t: Task, name: str) -> str:
    v = getattr(t, name)
    return ", ".join(v) if isinstance(v, list) else str(v or "").strip()


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def match_tasks(stored: list[Task], new: list[Task], embedder=None) -> tuple[dict[str, str], dict[str, str]]:
    """Pair new rows with stored tasks. Returns ({new id: stored id}, {new id: how})."""
    pairs: dict[str, str] = {}
    how: dict[str, str] = {}
    free = {t.id: t for t in stored}

    def take(n: Task, s: Task, why: str):
        pairs[n.id], how[n.id] = s.id, why
        free.pop(s.id, None)

    # 1. Real work-item IDs (#123). Row ids (Sheet!R5) shift when rows are inserted, so they need a similar title.
    for n in new:
        s = free.get(n.id)
        if s and (n.id.startswith("#") or similarity(n.title, s.title) >= 60):
            take(n, s, "id")
    # 2. Same normalised title
    for n in new:
        if n.id in pairs:
            continue
        s = next((s for s in free.values() if normalise(s.title) == normalise(n.title)), None)
        if s:
            take(n, s, "title")
    # 3. Same meaning (embeddings), else fuzzy title
    rest_new = [n for n in new if n.id not in pairs]
    if rest_new and free:
        rest_old = list(free.values())
        if embedder is not None:
            vecs = embedder.embed([f"{t.title}\n{t.description}" for t in rest_new + rest_old])
            nv, ov = vecs[:len(rest_new)], vecs[len(rest_new):]
            scored = sorted(((_cosine(a, b), i, j) for i, a in enumerate(nv) for j, b in enumerate(ov)), reverse=True)
            threshold, why = SEMANTIC_MATCH, "semantic"
        else:
            scored = sorted(((similarity(n.title, o.title), i, j) for i, n in enumerate(rest_new)
                             for j, o in enumerate(rest_old)), reverse=True)
            threshold, why = FUZZY_MATCH, "fuzzy"
        used_new, used_old = set(), set()
        for score, i, j in scored:
            if score < threshold:
                break
            if i in used_new or j in used_old:
                continue
            used_new.add(i)
            used_old.add(j)
            take(rest_new[i], rest_old[j], why)
    return pairs, how


def diff_tasks(stored: list[Task], new: list[Task], embedder=None) -> SyncReport:
    pairs, how = match_tasks(stored, new, embedder)
    by_id = {t.id: t for t in stored}
    report = SyncReport(matches=pairs, how=how)
    for n in new:
        if n.id not in pairs:
            report.added.append({"excel_id": n.id, "title": n.title})
            continue
        s = by_id[pairs[n.id]]
        changes = [FieldChange(s.id, s.title, FIELD_LABEL.get(f, f), _value(s, f), _value(n, f), s.jira_key)
                   for f in SYNC_FIELDS if f not in ("dependency", "parent_title") and _value(s, f) != _value(n, f)]
        report.changed.extend(changes)
        if not changes:
            report.unchanged += 1
    matched = set(pairs.values())
    report.removed = [{"task_id": t.id, "title": t.title, "jira_key": t.jira_key} for t in stored if t.id not in matched]
    return report


def apply_sync(stored: list[Task], new: list[Task], report: SyncReport) -> list[Task]:
    """The task list after the sync, in the Excel's row order."""
    by_id = {t.id: t for t in stored}
    used_ids = set(by_id)
    final_id: dict[str, str] = {}
    for n in new:
        if n.id in report.matches:
            final_id[n.id] = report.matches[n.id]
        else:
            nid, k = n.id, 1
            while nid in used_ids:          # an Excel row id may collide with a stored task's id
                k += 1
                nid = f"{n.id}~{k}"
            used_ids.add(nid)
            final_id[n.id] = nid
    changed_fields: dict[str, list[str]] = {}
    for c in report.changed:
        changed_fields.setdefault(c.task_id, []).append(c.field)

    out = []
    for n in new:
        t = n.model_copy(deep=True)
        t.id = final_id[n.id]
        # dependencies / parents in the Excel refer to Excel ids: point them at the final ids
        t.parent_title = final_id.get(t.parent_title, t.parent_title)
        t.dependency = [final_id.get(d, d) for d in t.dependency]
        old = by_id.get(t.id)
        if old:
            t.category = old.category
            t.jira_key = old.jira_key
            if old.jira_key:
                fields = set(old.pending_update) | set(changed_fields.get(t.id, []))
                t.pending_update = sorted(f for f in fields if f in ("title", "description", "acceptanceCriteria",
                                                                      "assignee", "estimate"))
        out.append(t)
    return out
