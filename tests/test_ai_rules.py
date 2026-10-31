import json
from types import SimpleNamespace

from delivery_agent.ai_rules import RuleAssessor
from delivery_agent.models import Task


class FakeClient:
    """Records requests and answers like the gateway's Chat Completions API."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(kwargs)
        content = json.dumps({"assessments": self.answers.pop(0)})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")])


def reading(id, category="BE", max_hours=None, default_hours=16, reason="rule X"):
    return {"id": id, "category": category, "category_reason": reason, "max_hours": max_hours,
            "default_hours": default_hours, "estimate_rule": "estimate rule"}


def test_request_carries_rules_and_schema():
    client = FakeClient([[reading("T1")]])
    RuleAssessor("Payment tasks are BE.", client=client).assess([Task(id="T1", title="VNPay checkout", estimate="8h")])
    req = client.requests[0]
    user = req["messages"][1]["content"]
    assert "Payment tasks are BE." in user and "VNPay checkout" in user
    fmt = req["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert "Review Code" in fmt["json_schema"]["schema"]["properties"]["assessments"]["items"]["properties"]["category"]["enum"]
    assert req["temperature"] == 0


def test_estimate_comparison_is_done_in_code():
    client = FakeClient([[reading("A", "Management", max_hours=2, default_hours=4),
                          reading("B", "Management", max_hours=2, default_hours=4),
                          reading("C", "Create Test Cases", default_hours=8)]])
    tasks = [Task(id="A", title="Weekly meeting", estimate="3h"), Task(id="B", title="Daily", estimate="1h"),
             Task(id="C", title="Write test cases", estimate="")]
    res = RuleAssessor("rules", client=client).assess(tasks)
    assert (res["A"].estimate_ok, res["A"].suggested_estimate) == (False, "2h") and "vượt" in res["A"].estimate_reason
    assert res["B"].estimate_ok is True
    assert (res["C"].estimate_ok, res["C"].suggested_estimate) == (False, "8h")


def test_cache_retry_and_unknown_category():
    client = FakeClient([[reading("A", "Nonsense")], [reading("B")]])   # first answer skips B -> one retry
    assessor = RuleAssessor("rules", client=client)
    tasks = [Task(id="A", title="a"), Task(id="B", title="b")]
    res = assessor.assess(tasks)
    assert res["A"].category == "Other" and res["B"].category == "BE"
    assert len(client.requests) == 2
    assessor.assess([Task(id="A", title="a", estimate="5h")])   # only the estimate changed
    assert len(client.requests) == 2                             # served from cache: estimates never reach the AI
