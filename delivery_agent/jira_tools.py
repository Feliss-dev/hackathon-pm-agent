"""Every tool of the Jira MCP server, offered to the chat agent, with a human gate on writes.

- `JiraBridge`   one MCP connection kept open on a background event loop (starting `uvx mcp-atlassian` for each call
                 would take seconds each time). Tools are discovered from the server, not hard-coded.
- `JiraToolbox`  turns the server's tools into function-calling schemas and applies the write policy
                 (`jira.agent_write_policy` in settings.yaml):
                   approve (default)  read tools run at once; write tools are queued as pending actions and only run
                                      after a person approves them (UI panel or `decide`)
                   allow              write tools run at once
                   deny               write tools are refused
  A tool is a read tool only when the server marks it `readOnlyHint=true`; anything else counts as a write.
"""
from __future__ import annotations

import asyncio
import atexit
import json
import threading
import uuid
from collections.abc import Callable

from .jira_mcp import JiraError, McpJira
from .tools import _inline

POLICIES = ("approve", "allow", "deny")
DESCRIPTION_MAX = 400


class JiraBridge:
    """One MCP session kept open on its own thread/event loop; `call` is safe from any thread."""

    def __init__(self, config: dict, server=None):
        self.config, self.server = config, server
        self.tools: list = []
        self.error: BaseException | None = None
        self._jira: McpJira | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stop: asyncio.Event | None = None

    def start(self, timeout: float = 120) -> "JiraBridge":
        if self._thread:
            if self.error:
                raise self.error
            return self
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="jira-mcp", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise JiraError(f"Jira MCP server did not answer within {timeout:.0f}s")
        if self.error:
            raise self.error
        return self

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main())

    async def _main(self) -> None:
        self._stop = asyncio.Event()
        try:
            # the connection is opened and closed by this one task (anyio requires that)
            async with McpJira(self.config, self.server) as jira:
                self._jira = jira
                self.tools = (await jira.client.list_tools()).tools
                self._ready.set()
                await self._stop.wait()
        except BaseException as exc:  # reported to the caller of start()
            self.error = exc
            self._ready.set()

    def call(self, name: str, args: dict, timeout: float = 120):
        if not self._thread or self.error:
            raise JiraError("Jira MCP connection is not open")
        return asyncio.run_coroutine_threadsafe(self._jira._call(name, args), self._loop).result(timeout)

    def close(self) -> None:
        if self._loop and self._stop and self._thread and self._thread.is_alive():
            self._loop.call_soon_threadsafe(self._stop.set)
            self._thread.join(10)


_shared: JiraBridge | None = None


def shared_bridge(config: dict) -> JiraBridge:
    """The process-wide connection (Streamlit reruns and new chats reuse it)."""
    global _shared
    if _shared is None or _shared.error:
        _shared = JiraBridge(config)
        atexit.register(_shared.close)
    return _shared.start()


def is_read_only(tool) -> bool:
    return bool(getattr(getattr(tool, "annotations", None), "read_only_hint", None))


class JiraToolbox:
    def __init__(self, bridge: JiraBridge, policy: str = "approve", audit: Callable[..., None] | None = None):
        if policy not in POLICIES:
            raise ValueError(f"jira.agent_write_policy must be one of {POLICIES}, got '{policy}'")
        self.bridge, self.policy, self.audit = bridge, policy, audit
        self.tools = {t.name: t for t in bridge.tools}
        self.actions: dict[str, dict] = {}      # action_id -> {tool, args, status, result|error, decided_by}

    @property
    def names(self) -> set[str]:
        return set(self.tools)

    def is_write(self, name: str) -> bool:
        return not is_read_only(self.tools[name])

    def schemas(self) -> list[dict]:
        out = []
        for t in self.tools.values():
            params = _inline(dict(t.input_schema or {}))
            params.setdefault("type", "object")
            params.setdefault("properties", {})
            kind = "WRITE - needs human approval. " if self.is_write(t.name) and self.policy == "approve" else ""
            out.append({"type": "function", "function": {
                "name": t.name, "description": (kind + (t.description or t.name))[:DESCRIPTION_MAX], "parameters": params}})
        return out

    def _log(self, step: str, reason: str, data: dict | None = None) -> None:
        if self.audit:
            self.audit(step, reason=reason, data=data or {})

    def _missing(self, name: str, args: dict) -> list[str]:
        return [k for k in (self.tools[name].input_schema or {}).get("required", []) if k not in args]

    def call(self, name: str, args: dict | None) -> dict:
        """Run a read tool now; queue, run or refuse a write tool according to the policy."""
        args = dict(args or {})
        if name not in self.tools:
            return {"error": f"Unknown Jira tool '{name}'"}
        if missing := self._missing(name, args):
            return {"error": f"Invalid arguments for {name}: missing {', '.join(missing)}"}
        if not self.is_write(name) or self.policy == "allow":
            return self._execute(name, args)
        if self.policy == "deny":
            return {"error": f"{name} changes Jira and the policy (jira.agent_write_policy) forbids the agent to do that"}
        action_id = uuid.uuid4().hex[:8]
        self.actions[action_id] = {"id": action_id, "tool": name, "args": args, "status": "pending"}
        self._log("jira_action_proposed", f"{name} {json.dumps(args, ensure_ascii=False, default=str)[:300]}",
                  {"action_id": action_id})
        return {"status": "pending_approval", "action_id": action_id,
                "message": "NOT executed yet. A person must approve this action in the 'Jira actions' panel. "
                           "Do not say it was done until the result shows it was executed."}

    def _execute(self, name: str, args: dict) -> dict:
        try:
            result = self.bridge.call(name, args)
        except Exception as exc:
            self._log("jira_tool", f"{name} failed: {exc}", {"ok": False})
            return {"error": f"{type(exc).__name__}: {exc}"}
        self._log("jira_tool", f"{name} {json.dumps(args, ensure_ascii=False, default=str)[:300]}", {"ok": True})
        return {"result": result}

    def pending(self) -> list[dict]:
        return [a for a in self.actions.values() if a["status"] == "pending"]

    def decide(self, action_id: str, approve: bool, approver: str = "") -> dict:
        action = self.actions.get(action_id)
        if not action or action["status"] != "pending":
            return {"error": f"No pending action '{action_id}'"}
        action["decided_by"] = approver
        if not approve:
            action["status"] = "rejected"
            self._log("jira_action_rejected", f"{action['tool']} rejected by {approver or '?'}", {"action_id": action_id})
            return {"status": "rejected"}
        out = self._execute(action["tool"], action["args"])
        action["status"] = "failed" if "error" in out else "executed"
        action.update(out)
        self._log("jira_action_approved", f"{action['tool']} approved by {approver or '?'} -> {action['status']}",
                  {"action_id": action_id})
        return {"status": action["status"]} | out
