"""Data models shared by every pipeline step."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CATEGORIES = [
    "BA", "SA", "FE", "BE", "UT", "Testing", "Migration", "Projection", "Review Code",
    "Communication", "Management", "Document", "Backfill", "Dev Study", "Execute Test",
    "Create Test Cases", "Baseline Branch", "Other",
]

Severity = Literal["High", "Medium", "Low"]


class Task(BaseModel):
    """One extracted task. JSON uses the camelCase keys from the spec."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = ""
    title: str = ""
    description: str = ""
    acceptance_criteria: str = Field("", alias="acceptanceCriteria")
    assignee: str = ""
    dependency: list[str] = Field(default_factory=list)
    category: str = ""
    estimate: str = ""
    story: str = ""             # grouping hint for the Jira hierarchy
    parent_title: str = Field("", alias="parentTitle")  # set => becomes a Subtask of that task
    source: str = ""            # file / sheet / row it came from
    jira_key: str = Field("", alias="jiraKey")                     # set once the task exists in Jira
    pending_update: list[str] = Field(default_factory=list, alias="pendingUpdate")  # fields changed since push

    def dump(self) -> dict:
        return self.model_dump(by_alias=True)


class EstimateFinding(BaseModel):
    task_id: str
    task: str
    rule: str
    severity: Severity
    current_estimate: str = Field("", alias="currentEstimate")
    suggested_estimate: str = Field("", alias="suggestedEstimate")
    reason: str

    model_config = ConfigDict(populate_by_name=True)


class ValidationIssue(BaseModel):
    task_id: str
    task: str
    kind: Literal["Missing Field", "Possible Duplicate", "Invalid Value"]
    severity: Severity
    detail: str
    match_key: str = ""      # Jira key or task id of the duplicate
    match_title: str = ""
    score: float = 0.0
    scope: str = ""          # batch | jira | epic | history (semantic match with an earlier run)


class PlannedIssue(BaseModel):
    ref: str                 # local reference, e.g. "EPIC", "S1", "T3"
    task_id: str = ""        # source task id (empty for epics/stories)
    issue_type: str
    type_name: str
    summary: str
    description: str = ""
    assignee: str = ""
    estimate_hours: float | None = None
    labels: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    parent_ref: str = ""     # ref of the parent planned issue
    parent_key: str = ""     # existing Jira key used as parent
    epic_key: str = ""       # existing Jira epic key
    sprint_id: int | None = None
    depends_on_refs: list[str] = Field(default_factory=list)
    existing_key: str = ""   # set when this node already exists in Jira (no create)
    skip_reason: str = ""    # e.g. possible duplicate the reviewer chose to skip
    update_fields: list[str] = Field(default_factory=list)  # existing issue whose fields changed (Excel sync)


class AuditEntry(BaseModel):
    timestamp: str
    step: str
    task: str = ""
    task_id: str = ""
    classification: str = ""
    estimate: str = ""
    jira_type: str = Field("", alias="jiraType")
    parent: str = ""
    reason: str = ""
    data: dict = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True)
