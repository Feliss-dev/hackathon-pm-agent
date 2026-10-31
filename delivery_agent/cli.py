"""Command line entry point:  python -m delivery_agent <command> ..."""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from .approval import ApprovalError, prompt_decision, record_decision, require_approval
from .audit import AuditLog
from .ai_rules import RuleAssessor
from .config import load_config, load_rules
from .excel_io import update_product_column
from .extract import extract
from .planner import build_plan, render_markdown
from .jira_mcp import load_existing
from .push import push_with_mcp
from .workshop import validate_package


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_plan(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _approve_interactively(plan_path: Path, plan: dict, audit: AuditLog, approver: str | None) -> bool:
    if not sys.stdin.isatty():
        print(f"\nNo interactive terminal: review {plan_path.with_suffix('.md').name}, then run:\n"
              f"  python -m delivery_agent approve {plan_path}")
        return False
    approved = prompt_decision(plan["summary"]["lines"])
    note = input("Comment (optional): ").strip()
    record_decision(plan_path, plan, approver or getpass.getuser(), approved, audit, note)
    print("Approved." if approved else "Rejected - nothing will be sent to Jira.")
    return approved


def _assessor(args, config) -> RuleAssessor:
    """Applies the human-written rules (config/rules.md + config/projects/<profile>.md) with the AI."""
    return RuleAssessor(load_rules(args.profile), hours_per_day=config["estimation"].get("hours_per_day", 8))


def cmd_plan(args, config) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    audit = AuditLog(out / "audit.jsonl")
    if args.project:
        config["jira"]["project_key"] = args.project

    print("Step 1/7  Extracting tasks ...")
    tasks = extract(args.inputs, config)
    _write_json(out / "tasks.json", [t.dump() for t in tasks])

    existing, checked = [], False
    if not args.offline:
        print("          Loading existing Jira issues for duplicate detection ...")
        existing = asyncio.run(load_existing(config, args.epic or ""))
        checked = True
    print("Step 2-5  Classifying, reviewing estimates, validating, building hierarchy ...")
    plan = build_plan(tasks, config, audit, assessor=_assessor(args, config), existing=existing, epic_key=args.epic or "", new_epic=args.new_epic or "",
                      sprint_id=args.sprint_id, include_duplicates=args.include_duplicates,
                      allow_incomplete=args.allow_incomplete, jira_checked=checked)
    plan_path = out / "plan.json"
    _write_json(plan_path, plan)
    (out / "plan.md").write_text(render_markdown(plan), encoding="utf-8")
    print(f"          Plan: {plan_path}   Review: {out / 'plan.md'}   Audit: {out / 'audit.jsonl'}")

    if args.no_prompt:
        print(f"\nStep 6/7  Awaiting human approval: python -m delivery_agent approve {plan_path}")
        return 0
    print("\nStep 6/7  Human approval")
    if not _approve_interactively(plan_path, plan, audit, args.approver):
        return 0
    if args.offline:
        print(f"Offline mode: push later with  python -m delivery_agent push {plan_path}")
        return 0
    return _do_push(plan_path, config, audit)


def cmd_approve(args, config) -> int:
    plan_path = Path(args.plan)
    plan = _load_plan(plan_path)
    audit = AuditLog(plan_path.parent / "audit.jsonl")
    md = plan_path.with_suffix(".md")
    if md.exists():
        print(md.read_text(encoding="utf-8"))
    approved = _approve_interactively(plan_path, plan, audit, args.approver)
    return 0 if approved or sys.stdin.isatty() else 1


def _do_push(plan_path: Path, config: dict, audit: AuditLog) -> int:
    plan = _load_plan(plan_path)
    try:
        decision = require_approval(plan_path, plan)
    except ApprovalError as exc:
        print(f"Refusing to push: {exc}")
        audit.record("push_refused", reason=str(exc))
        return 2
    config["jira"]["project_key"] = plan["project_key"]
    print(f"Step 7/7  Pushing to Jira (approved by {decision['approver']} at {decision['timestamp']}) ...")
    try:
        state = asyncio.run(push_with_mcp(plan, plan_path, config, audit))
    except Exception as exc:  # report partial progress; re-running resumes from the checkpoint
        audit.record("push_error", reason=str(exc))
        print(f"Push stopped: {exc}\nCreated so far is saved in {plan_path.stem}.result.json; re-run push to resume.")
        return 1
    print(f"Created {len(state['created'])} issues, {len(state['linked'])} links, skipped {len(state['skipped'])}.")
    for ref, key in state["created"].items():
        print(f"  {ref:>5} -> {key}")
    return 0


def cmd_push(args, config) -> int:
    plan_path = Path(args.plan)
    return _do_push(plan_path, config, AuditLog(plan_path.parent / "audit.jsonl"))


def cmd_excel(args, config) -> int:
    src = Path(args.workbook)
    dst = Path(args.output) if args.output else src.with_name(f"{src.stem}.classified{src.suffix}")
    if dst.resolve() == src.resolve():
        print("Output must differ from the input workbook.")
        return 2
    audit = AuditLog(Path(args.audit) if args.audit else dst.with_suffix(".audit.jsonl"))
    stats = update_product_column(src, dst, config, _assessor(args, config), audit,
                                  overwrite_existing=not args.keep_existing)
    print(json.dumps(stats, indent=2))
    print(f"Updated workbook: {dst}")
    return 0


def cmd_workshop(args, config) -> int:
    report = validate_package(args.path, config)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["readiness"] == "Ready" else 1


def cmd_classify(args, config) -> int:
    from .models import Task
    task = Task(id="probe", title=args.title, description=args.description, assignee=args.assignee, estimate=args.estimate)
    print(json.dumps(_assessor(args, config).assess([task])["probe"].dump(), indent=2, ensure_ascii=False))
    return 0


def cmd_runs(args, config) -> int:
    from .db import get_repository
    for r in get_repository().list_runs(None if args.all else config["jira"]["project_key"], args.limit):
        print(f"{r['run_id']}  {r['updated_at'][:16]}  {r['project_key']:<8} v{r['plan_version']:<3} "
              f"{r['tasks']:>4} tasks  {r['title'][:60]}")
    return 0


def cmd_sync(args, config) -> int:
    """Compare an edited Excel with the tasks of a stored run; --apply updates them and saves a new plan version."""
    from .agent import AgentSession
    session = AgentSession(config, offline=True, project=args.profile or "", run_id=args.run_id)
    result = session.sync_excel(args.workbook, apply=args.apply)
    print(json.dumps({k: v for k, v in result.items() if k != "plan"}, indent=2, ensure_ascii=False))
    if args.apply and result.get("applied"):
        print(f"\nNew plan version {session.plan.get('version')} saved; approve it again before pushing:\n"
              f"  python -m delivery_agent approve {session.plan_path}")
    return 0


def cmd_index_rules(args, config) -> int:
    from .config import read_common_rules, read_project_rules
    from .vectors import get_vector_store
    store = get_vector_store()
    print(f"common rules: {store.index_rules('__common__', read_common_rules())} chunks")
    if args.profile:
        print(f"{args.profile} rules: {store.index_rules(args.profile, read_project_rules(args.profile))} chunks")
    print(json.dumps(store.stats()))
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="delivery_agent", description="AI Delivery Planning & Jira Automation Agent")
    parser.add_argument("--config", help="technical settings YAML (default: config/settings.yaml)")
    parser.add_argument("--profile", help="project: rules config/projects/<PROFILE>.md + settings <PROFILE>.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("plan", help="Steps 1-6: extract, classify, review, validate, summarise, ask for approval")
    p.add_argument("inputs", nargs="+", help="BRD/design/workshop docs (.md .txt .docx .pdf) and/or Excel task lists")
    p.add_argument("--out", default="out", help="output folder")
    p.add_argument("--project", help="Jira project key (overrides config)")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--epic", help="existing Epic key to attach to, e.g. DT-1234")
    group.add_argument("--new-epic", help="create a new Epic with this summary")
    p.add_argument("--sprint-id", type=int, help="Jira sprint id for stories and tasks")
    p.add_argument("--offline", action="store_true", help="do not contact Jira (no duplicate check, no push)")
    p.add_argument("--include-duplicates", action="store_true", help="create tasks flagged as possible duplicates")
    p.add_argument("--allow-incomplete", action="store_true", help="create tasks with missing required fields")
    p.add_argument("--no-prompt", action="store_true", help="stop after writing the plan; approve separately")
    p.add_argument("--approver", help="name recorded in the approval (default: OS user)")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("approve", help="Step 6: review a plan and approve or reject it")
    p.add_argument("plan")
    p.add_argument("--approver")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("push", help="Step 7: create the approved plan in Jira via MCP")
    p.add_argument("plan")
    p.set_defaults(func=cmd_push)

    p = sub.add_parser("excel-product", help="Fill the Product column of a task tracking workbook")
    p.add_argument("workbook")
    p.add_argument("-o", "--output")
    p.add_argument("--audit")
    p.add_argument("--keep-existing", action="store_true", help="do not overwrite non-empty Product cells")
    p.set_defaults(func=cmd_excel)

    p = sub.add_parser("workshop", help="Validate a workshop submission folder")
    p.add_argument("path")
    p.set_defaults(func=cmd_workshop)

    p = sub.add_parser("classify", help="Apply the rules to one task (category + estimate check)")
    p.add_argument("title")
    p.add_argument("--description", default="")
    p.add_argument("--assignee", default="")
    p.add_argument("--estimate", default="")
    p.set_defaults(func=cmd_classify)

    p = sub.add_parser("runs", help="List planning sessions stored in the database")
    p.add_argument("--all", action="store_true", help="every project, not only the configured one")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_runs)

    p = sub.add_parser("sync", help="Update a stored run's tasks from an edited Excel task list")
    p.add_argument("workbook")
    p.add_argument("--run-id", required=True, help="run id (or its first 8 characters) from `runs`")
    p.add_argument("--apply", action="store_true", help="apply the changes (default: only show them)")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("index-rules", help="(Re)build the vector index of the rules")
    p.set_defaults(func=cmd_index_rules)

    args = parser.parse_args(argv)
    return args.func(args, load_config(args.config, args.profile))
