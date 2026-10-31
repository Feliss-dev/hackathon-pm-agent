"""LangGraph agent (pause / resume / decline / round limit) and the LangChain parts (retriever, chain, tools)."""
import json
from types import SimpleNamespace

from langchain_core.runnables import RunnableLambda

import delivery_agent.graph as graph_mod
from delivery_agent.agent import run_turn
from delivery_agent.graph import GIVE_UP, AgentGraph
from delivery_agent.lc import ProjectRetriever, langchain_tools, rag_chain
from delivery_agent.tools import REGISTRY


class Scripted:
    """Model stand-in: the scripted tool calls one by one, then a text answer. `endless` keeps calling a tool."""

    def __init__(self, calls, endless=None):
        self.calls, self.endless, self.requests = list(calls), endless, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, model, messages, tools):
        self.requests.append(messages)
        call = self.calls.pop(0) if self.calls else self.endless
        if call is None:
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Xong.", tool_calls=None))])
        name, args = call
        raw = args if isinstance(args, str) else json.dumps(args)
        n = len(self.requests)
        tc = SimpleNamespace(id=f"c{n}", function=SimpleNamespace(name=name, arguments=raw),
                             model_dump=lambda: {"id": f"c{n}", "type": "function", "function": {"name": name, "arguments": raw}})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tc]))])


USER = {"role": "user", "content": "Phân tích"}


def test_pause_before_tools_leaves_history_clean_then_resume(make_session, workbook):
    s = make_session()
    s.add_file("tracking.xlsx", workbook.read_bytes())
    g = AgentGraph(s, client=Scripted([("analyze", {"files": ["tracking.xlsx"]})]), interrupt=True)
    messages = [dict(USER)]
    first = g.run(messages)
    assert [c["function"]["name"] for c in first.paused] == ["analyze"] and first.text == ""
    assert messages == [USER] and s.plan is None                       # nothing ran, caller's history untouched

    done = g.resume(approve=True)
    assert done.text == "Xong." and not done.paused and s.plan is not None
    assert [m["role"] for m in messages] == ["user", "assistant", "tool", "assistant"]


def test_decline_tells_the_model_and_skips_the_tool(make_session, workbook):
    s = make_session()
    s.add_file("tracking.xlsx", workbook.read_bytes())
    client = Scripted([("analyze", {"files": ["tracking.xlsx"]})])
    messages = [dict(USER)]
    answer = run_turn(messages, s, client=client, confirm=lambda calls: False)
    assert answer == "Xong." and s.plan is None
    tool_msg = next(m for m in messages if m["role"] == "tool")
    assert "declined" in tool_msg["content"]
    assert "declined" in json.dumps(client.requests[-1])               # the model saw the refusal


def test_confirm_callback_receives_the_pending_tool_calls(make_session):
    s = make_session()
    seen = []
    run_turn([dict(USER)], s, client=Scripted([("list_files", {})]),
             confirm=lambda calls: seen.append([c["function"]["name"] for c in calls]) or True)
    assert seen == [["list_files"]]


def test_round_limit_gives_up(make_session, monkeypatch):
    monkeypatch.setattr(graph_mod, "MAX_TOOL_ROUNDS", 2)
    s = make_session()
    answer = run_turn([dict(USER)], s, client=Scripted([], endless=("list_files", {})))
    assert answer == GIVE_UP


def test_bad_arguments_reach_the_model_as_errors(make_session):
    s = make_session()
    seen = []
    messages = [dict(USER)]
    client = Scripted([("update_tasks", '{"updates": [{"task_id": "T1", "field": "category", "value": "x"}]}'),
                       ("show_plan", "not json at all")])
    run_turn(messages, s, on_tool=lambda n, a, r: seen.append((n, "error" in r)), client=client)
    assert seen == [("update_tasks", True), ("show_plan", True)]
    tool_texts = [m["content"] for m in messages if m["role"] == "tool"]
    assert "Invalid arguments" in tool_texts[0] and "error" in tool_texts[1]


def test_langchain_tools_mirror_the_registry(make_session):
    s = make_session()
    seen = []
    tools = langchain_tools(s, sink=lambda n, a, r: seen.append(n))
    assert set(tools) == set(REGISTRY.tools)
    assert tools["show_plan"].args_schema is REGISTRY.tools["show_plan"].args
    out = json.loads(tools["list_files"].invoke({}))
    assert "samples" in out and seen == ["list_files"]


def test_retriever_chain_and_grounded_answer(make_session):
    s = make_session()
    s.add_file("brd.md", b"# Registration\nCustomers register with email and phone.\n\n# Payments\nPay by VNPay.")
    docs = ProjectRetriever(store=s.vectors, project_key=s.project_key, run_id=s.run_id, k=2).invoke("customers register email")
    assert docs and docs[0].metadata["filename"] == "brd.md" and "score" in docs[0].metadata

    prompts = []
    llm = RunnableLambda(lambda p: prompts.append(p.to_string()) or "Khách đăng ký bằng email [brd.md#0]")
    assert "brd.md#0" in rag_chain(ProjectRetriever(store=s.vectors, project_key=s.project_key, run_id=s.run_id), llm).invoke("register")
    assert "Passages" in prompts[0] and "[brd.md#" in prompts[0]

    s.rag_llm = RunnableLambda(lambda p: "Trả lời có trích dẫn [brd.md#0]")
    out = s.ask_requirements("customers register", k=2, answer=True)
    assert out["answer"].startswith("Trả lời") and out["passages"][0]["file"] == "brd.md"
    assert "answer" not in s.ask_requirements("customers register", k=2)
