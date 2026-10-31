"""Function calling: schemas generated from the typed registry, argument validation, the agent loop."""
import json
from types import SimpleNamespace

from delivery_agent.agent import run_turn
from delivery_agent.tools import REGISTRY


def test_schemas_are_plain_json_schema():
    schemas = REGISTRY.schemas()
    names = [s["function"]["name"] for s in schemas]
    assert len(names) == len(set(names))
    assert {"analyze", "sync_excel", "ask_requirements", "search_similar_tasks", "open_run"} <= set(names)
    text = json.dumps(schemas)
    assert "$ref" not in text and "$defs" not in text          # inlined for gateways without $ref support
    assess = REGISTRY.tools["assess_task"].schema()["function"]["parameters"]
    assert "title" in assess["properties"] and assess["required"] == ["title"]   # property named "title" kept
    upd = REGISTRY.tools["update_tasks"].schema()["function"]["parameters"]
    assert upd["properties"]["updates"]["items"]["properties"]["field"]["enum"][0] == "title"
    # Jira writes stay a human action
    assert not any("push" == n or n.startswith("create_") for n in names)


def test_invalid_arguments_go_back_to_the_model(make_session):
    s = make_session()
    _, result = REGISTRY.call(s, "update_tasks", '{"updates": [{"task_id": "T1", "field": "category", "value": "BE"}]}')
    assert result["error"].startswith("Invalid arguments for update_tasks")
    _, result = REGISTRY.call(s, "drop_database", "{}")
    assert "Unknown tool" in result["error"]
    _, result = REGISTRY.call(s, "list_files", "{}")
    assert "samples" in result
    calls = s.repo.tool_calls(s.run_id)
    assert [c["ok"] for c in calls] == [False, False, True]
    assert all(c["duration_ms"] >= 0 for c in calls)


class FakeChat:
    """Answers with a tool call first, then with text once it has seen the tool result."""

    def __init__(self, calls):
        self.calls, self.seen = list(calls), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, model, messages, tools):
        self.seen.append(messages)
        if self.calls:
            name, args = self.calls.pop(0)
            tc = SimpleNamespace(id=f"call{len(self.seen)}", function=SimpleNamespace(name=name, arguments=json.dumps(args)),
                                 model_dump=lambda n=name, a=args, i=len(self.seen): {
                                     "id": f"call{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}})
            msg = SimpleNamespace(content="", tool_calls=[tc])
        else:
            msg = SimpleNamespace(content="Xong: 14 task.", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def test_agent_loop_runs_tools_and_answers(make_session, workbook):
    s = make_session()
    s.add_file("tracking.xlsx", workbook.read_bytes())
    client = FakeChat([("analyze", {"files": ["tracking.xlsx"]}), ("show_plan", {"section": "skipped"})])
    seen = []
    messages = [{"role": "user", "content": "Phân tích file"}]
    answer = run_turn(messages, s, on_tool=lambda n, a, r: seen.append((n, "error" not in r)), client=client)
    assert answer == "Xong: 14 task."
    assert seen == [("analyze", True), ("show_plan", True)]
    assert [m["role"] for m in messages] == ["user", "assistant", "tool", "assistant", "tool", "assistant"]
    assert s.plan["summary"]["counts"]["tasks_extracted"] == 14
    assert '"has_plan": true' in client.seen[-1][0]["content"]      # CURRENT STATE refreshed every round
