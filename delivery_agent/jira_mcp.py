"""Jira access through an MCP server.

Connection is configured with environment variables:
  JIRA_MCP_URL      Streamable-HTTP endpoint of a Jira MCP server (optional JIRA_MCP_TOKEN -> Bearer header)
  JIRA_MCP_COMMAND  or a command that launches a stdio server, e.g. "uvx mcp-atlassian"
                    (that server reads JIRA_URL / JIRA_USERNAME / JIRA_API_TOKEN from the environment)

Tool names come from config `jira.mcp_tools`; argument shapes below follow sooperset/mcp-atlassian.
To use a different server, adjust `_create_args` / `_search_args` / `_link_args`.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
import tempfile
from contextlib import AsyncExitStack
from pathlib import Path

from .models import PlannedIssue
from .validate import ExistingIssue

_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
OPTIONAL_TOOLS = {"update_issue"}   # only needed when an Excel sync changed issues that already exist


class JiraError(RuntimeError):
    pass


def _find_key(obj) -> str:
    if isinstance(obj, dict):
        if isinstance(obj.get("key"), str) and _KEY_RE.fullmatch(obj["key"]):
            return obj["key"]
        for v in obj.values():
            if k := _find_key(v):
                return k
    elif isinstance(obj, list):
        for v in obj:
            if k := _find_key(v):
                return k
    return ""


def _get(d: dict, *paths, default=""):
    for path in paths:
        cur = d
        for part in path.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        if cur:
            return cur
    return default


def parse_search_result(payload) -> list[ExistingIssue]:
    issues = payload.get("issues", []) if isinstance(payload, dict) else payload or []
    out = []
    for it in issues:
        out.append(ExistingIssue(
            key=it.get("key", ""),
            summary=_get(it, "summary", "fields.summary"),
            issue_type=_get(it, "issue_type.name", "issuetype.name", "fields.issuetype.name", "issue_type"),
            epic_key=_get(it, "parent.key", "fields.parent.key", "epic_key", "epic.key"),
        ))
    return out


class McpJira:
    """Async context manager wrapping an MCP client session."""

    def __init__(self, config: dict, server=None):
        self.server = server  # optional explicit server/transport (used by tests); else from env
        self.cfg = config["jira"]
        self.tools = self.cfg["mcp_tools"]
        self._stack = AsyncExitStack()
        self.client = None

    async def __aenter__(self) -> "McpJira":
        from mcp import Client, StdioServerParameters

        url, command = os.getenv("JIRA_MCP_URL"), os.getenv("JIRA_MCP_COMMAND")
        if self.server is not None:
            server = self.server
        elif url:
            import httpx2
            from mcp.client.streamable_http import streamable_http_client
            headers = {"Authorization": f"Bearer {os.environ['JIRA_MCP_TOKEN']}"} if os.getenv("JIRA_MCP_TOKEN") else {}
            http = await self._stack.enter_async_context(httpx2.AsyncClient(headers=headers, timeout=60))
            server = streamable_http_client(url, http_client=http)
        elif command:
            parts = shlex.split(command, posix=os.name != "nt")
            from mcp.client.stdio import stdio_client
            params = StdioServerParameters(command=parts[0], args=parts[1:], env=dict(os.environ))
            server = stdio_client(params, errlog=self._server_log())
        else:
            raise JiraError("Set JIRA_MCP_URL or JIRA_MCP_COMMAND to reach a Jira MCP server")
        self.client = await self._stack.enter_async_context(Client(server))
        listed = (await self.client.list_tools()).tools
        self.schemas = {t.name: (t.input_schema or {}).get("properties", {}) for t in listed}
        missing = [n for role, n in self.tools.items() if n not in self.schemas and role not in OPTIONAL_TOOLS]
        if missing:
            raise JiraError(f"MCP server does not expose tools {missing}; available: {sorted(self.schemas)}")
        return self

    def _server_log(self):
        """Where the stdio server's stderr goes. A Jupyter kernel's stderr has no file descriptor to hand to a child
        process, so it gets a log file instead (otherwise starting the server fails with `fileno`)."""
        try:
            sys.stderr.fileno()
            return sys.stderr
        except (AttributeError, OSError, ValueError):   # io.UnsupportedOperation is an OSError
            path = Path(tempfile.gettempdir()) / "jira_mcp_server.log"
            return self._stack.enter_context(open(path, "a", encoding="utf-8"))

    def _coerce(self, tool: str, args: dict) -> dict:
        """Match the server's declared argument types, e.g. mcp-atlassian wants `additional_fields` as a JSON string."""
        props = getattr(self, "schemas", {}).get(tool, {})
        out = {}
        for key, value in args.items():
            declared = props.get(key, {})
            types = {declared.get("type")} | {x.get("type") for x in declared.get("anyOf", [])}
            if isinstance(value, (dict, list)) and "string" in types and not types & {"object", "array"}:
                value = json.dumps(value, ensure_ascii=False)
            out[key] = value
        return out

    async def __aexit__(self, *exc):
        await self._stack.aclose()

    async def _call(self, tool: str, args: dict):
        result = await self.client.call_tool(tool, self._coerce(tool, args))
        text = "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")
        if result.is_error:
            raise JiraError(f"{tool} failed: {text}")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return result.structured_content or {"text": text}

    # --- argument shapes (mcp-atlassian) ---------------------------------------------------------
    def _search_args(self, jql: str, limit: int, start: int) -> dict:
        return {"jql": jql, "fields": "summary,issuetype,parent,status", "limit": limit, "start_at": start}

    def _create_args(self, issue: PlannedIssue, parent_key: str) -> dict:
        extra: dict = {}
        if parent_key:
            extra["parent"] = parent_key
        labels = list(dict.fromkeys(issue.labels))
        if labels:
            extra["labels"] = labels
        if issue.estimate_hours:
            extra["timetracking"] = {"originalEstimate": f"{issue.estimate_hours:g}h"}
        if issue.sprint_id:
            extra[self.cfg["sprint_field"]] = issue.sprint_id
        args = {"project_key": self.cfg["project_key"], "summary": issue.summary,
                "issue_type": issue.issue_type, "description": issue.description}
        if issue.assignee:
            args["assignee"] = issue.assignee
        if issue.components:
            args["components"] = ",".join(issue.components)
        if extra:
            args["additional_fields"] = extra
        return args

    def _update_args(self, key: str, issue: PlannedIssue, fields: list[str]) -> dict:
        changes: dict = {}
        if "title" in fields:
            changes["summary"] = issue.summary
        if set(fields) & {"description", "acceptanceCriteria"}:
            changes["description"] = issue.description
        if "assignee" in fields:
            changes["assignee"] = issue.assignee or None
        args = {"issue_key": key, "fields": changes}
        if "estimate" in fields and issue.estimate_hours:
            args["additional_fields"] = {"timetracking": {"originalEstimate": f"{issue.estimate_hours:g}h"}}
        return args

    def _link_args(self, source: str, target: str, link_type: str) -> dict:
        # Check direction against your Jira link type: inward/outward naming differs between link types.
        return {"link_type": link_type, "inward_issue_key": source, "outward_issue_key": target}

    # --- operations ------------------------------------------------------------------------------
    async def search(self, jql: str, limit: int = 200) -> list[ExistingIssue]:
        found: list[ExistingIssue] = []
        while len(found) < limit:
            page = min(50, limit - len(found))
            payload = await self._call(self.tools["search"], self._search_args(jql, page, len(found)))
            batch = parse_search_result(payload)
            found.extend(batch)
            if len(batch) < page:
                break
        return found

    async def create_issue(self, issue: PlannedIssue, parent_key: str = "") -> str:
        payload = await self._call(self.tools["create_issue"], self._create_args(issue, parent_key))
        key = _find_key(payload) or (m.group(0) if (m := _KEY_RE.search(json.dumps(payload))) else "")
        if not key:
            raise JiraError(f"Could not read the new issue key from: {payload}")
        return key

    async def update_issue(self, key: str, issue: PlannedIssue, fields: list[str]) -> None:
        tool = self.tools.get("update_issue")
        if not tool or tool not in getattr(self, "schemas", {}):
            raise JiraError("The MCP server has no update tool (jira.mcp_tools.update_issue), so existing issues cannot be updated")
        await self._call(tool, self._update_args(key, issue, fields))

    async def link(self, source: str, target: str, link_type: str) -> None:
        await self._call(self.tools["link_issues"], self._link_args(source, target, link_type))


async def load_existing(config: dict, epic_key: str = "") -> list[ExistingIssue]:
    """Open issues of the project plus everything under the target epic, for duplicate detection."""
    jcfg = config["jira"]
    async with McpJira(config) as jira:
        found = await jira.search(jcfg["duplicate_search_jql"].format(project=jcfg["project_key"]),
                                  jcfg.get("duplicate_search_limit", 200))
        if epic_key:  # include everything under the target epic, even if done or outside the JQL above
            children = await jira.search(f"parent = {epic_key}", 200)
            for child in children:
                child.epic_key = child.epic_key or epic_key
            child_keys = {c.key for c in children}
            found = [f for f in found if f.key not in child_keys] + children
        return found
