"""The chat agent as a LangGraph state graph.

    START -> agent --(tool calls)--> tools --> agent ... --> END          (agent: the model, tools: function calls)

- state          `messages` (OpenAI chat format), `pending` (tool calls waiting to run), `rounds` (model calls so far)
- tools node     runs each call through the LangChain `StructuredTool` wrappers (`lc.langchain_tools`); a call the wrapper
                 cannot parse (bad JSON, unknown / Jira MCP tool) goes straight to `REGISTRY.call`, which turns any error
                 into an `{"error": ...}` result for the model
- checkpointer   `MemorySaver`, one thread per turn
- human gate     with `interrupt=True` the graph stops before the tools node. `run` returns the pending tool calls;
                 `resume(approve=True)` runs them, `resume(approve=False)` tells the model the user declined
- `messages` handed to `run` is extended in place only when the turn is finished, so an abandoned pause leaves the
  conversation clean
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from .agent import MAX_TOOL_ROUNDS, SYSTEM_PROMPT
from .lc import TOOL_RESULT_MAX, langchain_tools
from .llm import chat_model, get_client, with_retry

GIVE_UP = "Tôi đã chạy nhiều bước nhưng chưa hoàn tất. Bạn có thể nói rõ hơn yêu cầu không?"


class AgentState(TypedDict):
    messages: list
    pending: list
    rounds: int


@dataclass
class TurnResult:
    text: str = ""
    paused: list[dict] = field(default_factory=list)   # tool calls waiting for a person (interrupt mode)


class AgentGraph:
    def __init__(self, session, client=None, interrupt: bool = False,
                 on_tool: Callable[[str, dict, dict], None] | None = None):
        self.session, self.client, self.on_tool = session, client, on_tool
        self._lc_tools = langchain_tools(session, sink=self._sink)
        self._thread = {"configurable": {"thread_id": uuid.uuid4().hex}, "recursion_limit": 4 * MAX_TOOL_ROUNDS + 10}
        self._target: list[dict] | None = None
        self.graph = self._build(interrupt)

    # ------------------------------------------------------------ nodes
    def _sink(self, name: str, args: dict, result: dict) -> None:
        if self.on_tool:
            self.on_tool(name, args, result)

    def _agent(self, state: AgentState) -> dict:
        from .tools import REGISTRY  # noqa: F401  (registers the tools)
        s = self.session
        system = f"{SYSTEM_PROMPT}\n\nCURRENT STATE (JSON): {json.dumps(s.state(), ensure_ascii=False)}"
        client = self.client or get_client()
        response = with_retry(lambda: client.chat.completions.create(
            model=chat_model(), messages=[{"role": "system", "content": system}] + state["messages"],
            tools=s.agent_tool_schemas()),                    # own tools + the Jira MCP server's, re-read every round
            what="chat", on_retry=lambda msg: s.audit.record("llm_retry", reason=msg))
        msg = response.choices[0].message
        out = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            out["tool_calls"] = [tc.model_dump() for tc in msg.tool_calls]
        return {"messages": state["messages"] + [out], "pending": out.get("tool_calls", []), "rounds": state["rounds"] + 1}

    def _run_call(self, tc: dict) -> str:
        from .tools import REGISTRY
        name, raw = tc["function"]["name"], tc["function"]["arguments"]
        tool = self._lc_tools.get(name)
        if tool is not None:
            try:
                return tool.invoke(json.loads(raw or "{}"))
            except Exception:       # arguments the wrapper cannot parse: let the registry report it to the model
                pass
        args, result = REGISTRY.call(self.session, name, raw)
        self._sink(name, args, result)
        return json.dumps(result, ensure_ascii=False, default=str)[:TOOL_RESULT_MAX]

    def _tools(self, state: AgentState) -> dict:
        msgs = list(state["messages"])
        for tc in state["pending"]:
            msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": self._run_call(tc)})
        return {"messages": msgs, "pending": []}

    def _give_up(self, state: AgentState) -> dict:
        return {"messages": state["messages"] + [{"role": "assistant", "content": GIVE_UP}], "pending": []}

    def _build(self, interrupt: bool):
        g = StateGraph(AgentState)
        g.add_node("agent", self._agent)
        g.add_node("tools", self._tools)
        g.add_node("give_up", self._give_up)
        g.add_edge(START, "agent")
        g.add_conditional_edges("agent", lambda s: "tools" if s["pending"] else END)
        g.add_conditional_edges("tools", lambda s: "give_up" if s["rounds"] >= MAX_TOOL_ROUNDS else "agent")
        g.add_edge("give_up", END)
        return g.compile(checkpointer=MemorySaver(), interrupt_before=["tools"] if interrupt else [])

    # ------------------------------------------------------------ driving
    def _result(self) -> TurnResult:
        snap = self.graph.get_state(self._thread)
        if snap.next:                                           # stopped before the tools node
            return TurnResult(paused=list(snap.values["pending"]))
        messages = snap.values["messages"]
        self._target[:] = messages                               # turn finished: update the caller's history
        last = next((m for m in reversed(messages) if m["role"] == "assistant"), {"content": ""})
        return TurnResult(text=last["content"])

    def run(self, messages: list[dict]) -> TurnResult:
        self._target = messages
        self.graph.invoke({"messages": list(messages), "pending": [], "rounds": 0}, self._thread)
        return self._result()

    def resume(self, approve: bool = True) -> TurnResult:
        if not approve:
            snap = self.graph.get_state(self._thread)
            declined = [{"role": "tool", "tool_call_id": tc["id"],
                         "content": json.dumps({"error": "The user declined to run this tool."})}
                        for tc in snap.values["pending"]]
            self.graph.update_state(self._thread, {"messages": snap.values["messages"] + declined, "pending": []},
                                    as_node="tools")
        self.graph.invoke(None, self._thread)
        return self._result()
