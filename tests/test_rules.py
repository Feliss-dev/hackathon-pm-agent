import pytest

from delivery_agent.estimate import parse_hours
from delivery_agent.models import Task
from delivery_agent.validate import ExistingIssue, Validator


@pytest.mark.parametrize("value,hours", [("24h", 24), ("3d", 24), ("1w", 40), ("90m", 1.5), ("6", 6), (5, 5), ("2,5h", 2.5), ("", None), ("abc", None)])
def test_parse_hours(value, hours):
    assert parse_hours(value) == hours


def test_required_fields(config):
    issues = Validator(config).required_fields(Task(id="T1", title="X", category="BE"))
    by_detail = {i.detail: i.severity for i in issues}
    assert by_detail == {"Required field 'assignee' is empty - will be created unassigned": "Medium",
                         "Required field 'estimate' is empty": "High"}


def test_duplicates_batch_jira_and_epic(config):
    tasks = [Task(id="T1", title="Create Customer API"), Task(id="T2", title="Create customer API!"),
             Task(id="T3", title="Build registration screen"), Task(id="T4", title="Totally new thing")]
    existing = [ExistingIssue("DT-9", "Build the registration screen", "Story", epic_key="DT-1"),
                ExistingIssue("DT-10", "Create Customer API", "Task")]
    issues = Validator(config).duplicates(tasks, existing, epic_key="DT-1")
    scopes = {(i.task_id, i.scope, i.match_key) for i in issues}
    assert ("T2", "batch", "T1") in scopes
    assert ("T3", "epic", "DT-9") in scopes
    assert ("T1", "jira", "DT-10") in scopes
    assert not any(i.task_id == "T4" for i in issues)
