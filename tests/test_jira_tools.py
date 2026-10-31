"""Jira MCP tools for the agent: discovery, schemas, read vs write, the human gate and the write policy."""
import json
from types import SimpleNamespace

from delivery_agent.jira_tools import JiraToolbox
from delivery_agent.tools import REGISTRY


def mcp_tool(name, required=(), read_only=None, description=""):
    ann = SimpleNamespace(read_only_hint=read_only) if read_only is not None else None
    schema = {"type": "object", "properties": {k: {"type": "string"} for k in required}, "required": list(required)}
    return SimpleNamespace(name=name, description=description or f"{name} tool", input_schema=schema, annotations=ann)


class FakeBridge:
    def __init__(self):
        self.tools = [mcp_tool("jira_get_issue", ["issue_key"], read_only=True),
                      mcp_tool("jira_add_comment", ["issue_key", "body"]),
                      mcp_tool("jira_delete_issue", ["issue_key"], read_only=False)]
        self.calls = []

    def start(self):
        return self

    def call(self, name, args, timeout=120):
        self.calls.append((name, args))
        if name == "jira_add_comment" and args.get("body") == "boom":
            raise RuntimeError("Jira said no")
        return {"key": args.get("issue_key"), "ok": True}


def test_schemas_list_every_server_tool_and_mark_writes(make_session):
    s = make_session(jira_bridge=FakeBridge())
    names = [x["function"]["name"] for x in s.agent_tool_schemas()]
    assert {"analyze", "jira_get_issue", "jira_add_comment", "jira_delete_issue"} <= set(names)
    assert len(names) == len(set(names))
    by = {x["function"]["name"]: x["function"] for x in s.agent_tool_schemas()}
    assert by["jira_add_comment"]["description"].startswith("WRITE")
    assert not by["jira_get_issue"]["description"].startswith("WRITE")
    assert by["jira_add_comment"]["parameters"]["required"] == ["issue_key", "body"]


def test_read_runs_at_once_write_waits_for_a_person(make_session):
    bridge = FakeBridge()
    s = make_session(jira_bridge=bridge)
    _, read = REGISTRY.call(s, "jira_get_issue", {"issue_key": "KAN-1"})
    assert read["result"]["key"] == "KAN-1" and bridge.calls == [("jira_get_issue", {"issue_key": "KAN-1"})]

    _, write = REGISTRY.call(s, "jira_add_comment", '{"issue_key": "KAN-1", "body": "hello"}')
    assert write["status"] == "pending_approval" and len(bridge.calls) == 1          # nothing sent to Jira yet
    assert [a["tool"] for a in s.pending_jira_actions()] == ["jira_add_comment"]
    assert s.state()["jira_tools"]["pending_actions"][0]["id"] == write["action_id"]

    done = s.decide_jira_action(write["action_id"], True, "Lan")
    assert done["status"] == "executed" and bridge.calls[-1][0] == "jira_add_comment"
    assert s.pending_jira_actions() == []
    assert s.decide_jira_action(write["action_id"], True, "Lan")["error"]            # cannot run twice


def test_reject_and_failure_and_bad_arguments(make_session):
    bridge = FakeBridge()
    s = make_session(jira_bridge=bridge)
    _, w = REGISTRY.call(s, "jira_delete_issue", {"issue_key": "KAN-9"})
    assert s.decide_jira_action(w["action_id"], False, "Lan")["status"] == "rejected"
    assert all(c[0] != "jira_delete_issue" for c in bridge.calls)

    _, w = REGISTRY.call(s, "jira_add_comment", {"issue_key": "KAN-1", "body": "boom"})
    out = s.decide_jira_action(w["action_id"], True, "Lan")
    assert out["status"] == "failed" and "Jira said no" in out["error"]

    _, bad = REGISTRY.call(s, "jira_add_comment", {"issue_key": "KAN-1"})            # missing body: never queued
    assert "missing body" in bad["error"] and s.pending_jira_actions() == []
    _, unknown = REGISTRY.call(s, "jira_fly_to_moon", {})
    assert "Unknown tool" in unknown["error"]


def test_write_policy_allow_and_deny(config):
    bridge = FakeBridge()
    allow = JiraToolbox(bridge, "allow")
    assert allow.call("jira_add_comment", {"issue_key": "KAN-1", "body": "x"})["result"]["ok"]
    deny = JiraToolbox(bridge, "deny")
    assert "forbids" in deny.call("jira_add_comment", {"issue_key": "KAN-1", "body": "x"})["error"]
    assert deny.call("jira_get_issue", {"issue_key": "KAN-1"})["result"]["ok"]       # reads still work
    assert config["jira"]["agent_write_policy"] == "approve"                         # safe default


def test_decisions_are_audited(make_session):
    s = make_session(jira_bridge=FakeBridge())
    _, w = REGISTRY.call(s, "jira_add_comment", {"issue_key": "KAN-1", "body": "hi"})
    s.decide_jira_action(w["action_id"], True, "Lan")
    steps = [json.loads(line)["step"] for line in (s.run_dir / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {"jira_action_proposed", "jira_action_approved", "jira_tool"} <= set(steps)


def test_no_jira_connection_means_no_jira_tools(make_session):
    s = make_session()                                   # offline, no bridge
    assert s.jira_toolbox is None
    assert not any(x["function"]["name"].startswith("jira_") for x in s.agent_tool_schemas())
