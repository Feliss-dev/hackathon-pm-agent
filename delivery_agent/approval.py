"""Step 6 - human approval gate.

An approval is a separate file bound to the SHA-256 of the plan it approves, so a plan edited after
approval is rejected at push time. There is deliberately no flag that bypasses this.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .audit import AuditLog


def plan_hash(plan: dict) -> str:
    return hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def approval_path(plan_path: Path) -> Path:
    return plan_path.with_name(plan_path.stem + ".approval.json")


def prompt_decision(summary_lines: list[str], input_fn=input, out=sys.stdout) -> bool:
    out.write("\n" + "\n".join(summary_lines) + "\n\nContinue to Jira creation?\n[A] Approve\n[R] Reject\n")
    while True:
        answer = input_fn("> ").strip().lower()
        if answer in ("a", "approve"):
            return True
        if answer in ("r", "reject"):
            return False
        out.write("Please type A (Approve) or R (Reject).\n")


def record_decision(plan_path: Path, plan: dict, approver: str, approved: bool, audit: AuditLog, note: str = "",
                    on_record=None) -> dict:
    """Write the decision next to the plan; `on_record(decision)` also stores it in the database."""
    decision = {
        "plan_hash": plan_hash(plan),
        "decision": "approved" if approved else "rejected",
        "approver": approver,
        "note": note,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    approval_path(plan_path).write_text(json.dumps(decision, indent=2), encoding="utf-8")
    if on_record:
        on_record(decision)
    audit.record("approval", reason=f"Plan {decision['decision']} by {approver}", data=decision)
    return decision


class ApprovalError(RuntimeError):
    pass


def require_approval(plan_path: Path, plan: dict) -> dict:
    path = approval_path(plan_path)
    if not path.exists():
        raise ApprovalError(f"No approval found ({path.name}). Run `approve` first.")
    decision = json.loads(path.read_text(encoding="utf-8"))
    if decision.get("decision") != "approved":
        raise ApprovalError(f"Plan was {decision.get('decision')} by {decision.get('approver')}.")
    if decision.get("plan_hash") != plan_hash(plan):
        raise ApprovalError("Plan changed after it was approved. Review and approve it again.")
    return decision
