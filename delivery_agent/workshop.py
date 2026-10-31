"""Workshop deliverables validation and readiness score."""
from __future__ import annotations

import re
from pathlib import Path

from . import excel_io

TEXT_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".html", ".csv"}


def _matches(path: Path, keywords: list[str]) -> bool:
    name = path.stem.lower().replace("_", " ").replace("-", " ")
    return any(k.replace("_", " ").replace("-", " ") in name for k in keywords)


def validate_package(root: str | Path, config: dict) -> dict:
    root = Path(root)
    wcfg = config["workshop"]
    files = [p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts]

    source_files = [p for p in files if p.suffix.lower() in wcfg["source_extensions"]]
    business = [p for p in files if p.suffix.lower() in {".md", ".txt", ".docx", ".pdf"} and _matches(p, wcfg["business_keywords"])]
    task_lists = [p for p in files if p.suffix.lower() in {".xlsx", ".xlsm", ".csv"} and _matches(p, wcfg["task_list_keywords"])]
    test_cases = [p for p in files if p.suffix.lower() in {".xlsx", ".xlsm", ".csv", ".docx", ".md", ".pdf"}
                  and _matches(p, wcfg["test_case_keywords"])]
    slides = [p for p in files if p.suffix.lower() in wcfg["slides_extensions"]]

    gitlab_re = re.compile(wcfg["gitlab_url_pattern"], re.I)
    gitlab_urls: set[str] = set()
    for p in files:
        if p.suffix.lower() in TEXT_SUFFIXES and p.stat().st_size < 2_000_000:
            gitlab_urls.update(gitlab_re.findall(p.read_text(encoding="utf-8", errors="ignore")))

    estimate_detail, estimate_ok = "No task list to check", False
    xlsx_lists = [p for p in task_lists if p.suffix.lower() in (".xlsx", ".xlsm")]
    if xlsx_lists:
        tasks = [t for p in xlsx_lists for t in excel_io.read_tasks(p, config)]
        missing = [t.id for t in tasks if not t.estimate.strip()]
        estimate_ok = bool(tasks) and not missing
        estimate_detail = (f"{len(tasks)} tasks, all estimated" if estimate_ok
                           else f"{len(missing)} of {len(tasks)} tasks missing an estimate" if tasks
                           else "Task list has no recognisable task rows")

    rel = lambda ps: [str(p.relative_to(root)) for p in ps[:10]]
    checks = [
        {"check": "Source Code", "required": True, "passed": bool(source_files or gitlab_urls),
         "detail": f"{len(source_files)} source files" + (f"; GitLab: {sorted(gitlab_urls)[0]}" if gitlab_urls else ""),
         "files": rel(source_files)},
        {"check": "GitLab URL exists", "required": True, "passed": bool(gitlab_urls),
         "detail": ", ".join(sorted(gitlab_urls)) or "No GitLab URL found in text files"},
        {"check": "Business Description", "required": True, "passed": bool(business), "detail": "", "files": rel(business)},
        {"check": "Task List attached", "required": True, "passed": bool(task_lists), "detail": "", "files": rel(task_lists)},
        {"check": "Estimate completed", "required": True, "passed": estimate_ok, "detail": estimate_detail},
        {"check": "Test Cases", "required": True, "passed": bool(test_cases), "detail": "", "files": rel(test_cases)},
        {"check": "Presentation Slides", "required": False, "passed": bool(slides), "detail": "optional", "files": rel(slides)},
    ]
    required = [c for c in checks if c["required"]]
    passed = sum(c["passed"] for c in required)
    if passed == len(required):
        readiness = "Ready"
    elif passed >= len(required) / 2:
        readiness = "Partially Ready"
    else:
        readiness = "Not Ready"
    return {"package": str(root), "readiness": readiness, "score": f"{passed}/{len(required)}",
            "checks": checks, "missing": [c["check"] for c in required if not c["passed"]]}
