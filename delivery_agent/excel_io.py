"""Excel task-list reading and the Product-column-only workbook update."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl

from .audit import AuditLog
from .models import Task

_LOGICAL = {
    "title": "title_headers", "description": "description_headers", "assignee": "assignee_headers",
    "estimate": "estimate_headers", "acceptance": "acceptance_headers", "dependency": "dependency_headers",
    "story": "story_headers", "product": "product_header", "type": "type_headers", "id": "id_headers",
    "parent": "parent_headers", "state": "state_headers",
}
_TITLE_LEVEL_RE = re.compile(r"^title\s*(\d+)$")   # Azure DevOps tree export: Title 1, Title 2, ...


@dataclass
class SheetLayout:
    header_row: int
    columns: dict[str, int] = field(default_factory=dict)   # logical field -> 1-based column
    title_levels: list[int] = field(default_factory=list)   # columns of Title 1..N, in level order


def _norm(v) -> str:
    return str(v).strip().lower() if v is not None else ""


def find_layout(ws, xl_cfg: dict) -> SheetLayout | None:
    aliases = {name: {a.lower() for a in xl_cfg.get(key, [])} for name, key in _LOGICAL.items()}
    for row in ws.iter_rows(min_row=1, max_row=xl_cfg.get("header_scan_rows", 15)):
        cols: dict[str, int] = {}
        levels: list[tuple[int, int]] = []
        for cell in row:
            text = _norm(cell.value)
            if m := _TITLE_LEVEL_RE.match(text):
                levels.append((int(m.group(1)), cell.column))
                continue
            for name, names in aliases.items():
                if text in names and name not in cols:
                    cols[name] = cell.column
        if "title" in cols or levels:
            return SheetLayout(header_row=row[0].row, columns=cols, title_levels=[c for _, c in sorted(levels)])
    return None


def _cell(ws, row: int, layout: SheetLayout, name: str):
    col = layout.columns.get(name)
    return ws.cell(row=row, column=col).value if col else None


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def row_title(ws, r: int, layout: SheetLayout) -> tuple[str, int]:
    """(title, tree level). With Title 1..N columns the deepest filled one wins."""
    for level in range(len(layout.title_levels) - 1, -1, -1):
        if title := _text(ws.cell(row=r, column=layout.title_levels[level]).value):
            return title, level
    return _text(_cell(ws, r, layout, "title")), 0


def read_tasks(path: str | Path, config: dict) -> list[Task]:
    """Read tasks from every sheet that has a recognisable header row. Uses cached values for formulas.

    Rows whose Work Item Type is a container (Epic, Feature, User Story, ...) are not tasks: they become the
    `story` of the tasks below them. Parents come from the Parent/ID columns, or from the Title 1..N level.
    """
    xl_cfg = config["excel"]
    containers = {c.lower() for c in xl_cfg.get("container_types", [])}
    skip_states = {s.lower() for s in xl_cfg.get("skip_states", [])}
    wb = openpyxl.load_workbook(path, data_only=True, read_only=False)
    tasks: list[Task] = []
    for ws in wb.worksheets:
        layout = find_layout(ws, xl_cfg)
        if not layout:
            continue
        rows: list[dict] = []
        by_id: dict[str, dict] = {}
        stack: dict[int, dict] = {}
        for r in range(layout.header_row + 1, ws.max_row + 1):
            title, level = row_title(ws, r, layout)
            if not title:
                continue
            row = {"r": r, "title": title, "id": _text(_cell(ws, r, layout, "id")),
                   "type": _text(_cell(ws, r, layout, "type")), "state": _text(_cell(ws, r, layout, "state"))}
            row["container"] = row["type"].lower() in containers
            parent_id = _text(_cell(ws, r, layout, "parent"))
            row["parent"] = by_id.get(parent_id) or (stack.get(level - 1) if level > 0 else None)
            row["task_id"] = f"#{row['id']}" if row["id"] else f"{ws.title}!R{r}"
            stack[level] = row
            if row["id"]:
                by_id[row["id"]] = row
            rows.append(row)

        for row in rows:
            if row["container"] or row["state"].lower() in skip_states:
                continue
            r = row["r"]
            story = _text(_cell(ws, r, layout, "story"))
            ancestor = row["parent"]
            while not story and ancestor:
                if ancestor["container"]:
                    story = ancestor["title"]
                ancestor = ancestor["parent"]
            parent = row["parent"]
            deps = _text(_cell(ws, r, layout, "dependency"))
            tasks.append(Task(
                id=row["task_id"], title=row["title"],
                description=_text(_cell(ws, r, layout, "description")),
                acceptanceCriteria=_text(_cell(ws, r, layout, "acceptance")),
                assignee=_text(_cell(ws, r, layout, "assignee")),
                dependency=[d.strip() for d in deps.replace(";", ",").split(",") if d.strip()],
                estimate=_text(_cell(ws, r, layout, "estimate")),
                story=story,
                parentTitle=parent["task_id"] if parent and not parent["container"] else "",
                category=_text(_cell(ws, r, layout, "product")),
                source=f"{Path(path).name}:{ws.title}!R{r}",
            ))
    return tasks


def update_product_column(src: str | Path, dst: str | Path, config: dict, assessor,
                          audit: AuditLog | None = None, overwrite_existing: bool = True) -> dict:
    """Write the category (from the human-written rules, applied by the AI) into the Product column only.

    Formulas, styles, merged cells, other sheets and every other cell are left untouched because
    openpyxl round-trips them; we only assign `cell.value` on Product cells.
    """
    src, dst = Path(src), Path(dst)
    keep_vba = src.suffix.lower() == ".xlsm"
    wb = openpyxl.load_workbook(src, keep_vba=keep_vba)           # formulas kept as formulas
    cached = openpyxl.load_workbook(src, data_only=True)           # cached values for reading inputs
    stats = {"updated": 0, "unchanged": 0, "skipped_formula": 0, "skipped_existing": 0, "sheets_without_product": []}

    # Pass 1: collect the rows to classify, so the AI is called once for the whole workbook.
    targets: list[tuple] = []   # (cell, task)
    for ws in wb.worksheets:
        layout = find_layout(ws, config["excel"])
        if not layout:
            continue
        if "product" not in layout.columns:
            stats["sheets_without_product"].append(ws.title)
            continue
        ws_values = cached[ws.title]
        for r in range(layout.header_row + 1, ws.max_row + 1):
            title, _ = row_title(ws_values, r, layout)
            if not title:
                continue
            cell = ws.cell(row=r, column=layout.columns["product"])
            current = cell.value
            if isinstance(current, str) and current.startswith("="):
                stats["skipped_formula"] += 1
                continue
            if current not in (None, "") and not overwrite_existing:
                stats["skipped_existing"] += 1
                continue
            targets.append((cell, Task(id=f"{ws.title}!R{r}", title=title,
                                       description=_text(_cell(ws_values, r, layout, "description")),
                                       assignee=_text(_cell(ws_values, r, layout, "assignee")))))

    # Pass 2: apply the rules and write only the Product cells.
    assessments = assessor.assess([task for _, task in targets]) if targets else {}
    for cell, task in targets:
        a = assessments[task.id]
        previous = _text(cell.value)
        if previous == a.category:
            stats["unchanged"] += 1
        else:
            cell.value = a.category
            stats["updated"] += 1
        if audit:
            audit.record("excel_product_update", task=task.title, task_id=task.id, classification=a.category,
                         reason=f"Theo rule: {a.category_reason}", data={"cell": cell.coordinate, "previous": previous})

    dst.parent.mkdir(parents=True, exist_ok=True)
    wb.save(dst)
    return stats
