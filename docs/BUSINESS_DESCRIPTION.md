# Business Description — Team 3

**Topic:** A tool that reads requirements, breaks them into tasks, estimates and audits them, then imports them into Jira.

## Problem

Project managers spend hours turning BRDs and Excel task lists into Jira issues by hand:

- tasks are copied manually, and categories and estimates do not follow the team's conventions;
- the same work gets created twice, across sprints or by different people;
- when the Excel plan changes, Jira silently drifts away from it;
- nobody can explain later why a task got its category or its estimate.

## Solution

A chat agent (Streamlit) for PMs. A PM attaches a BRD or an Excel file and asks in Vietnamese. The agent:

1. **Extracts** tasks: the LLM reads documents; Excel files are read directly.
2. **Classifies and estimates** each task against **rules written by people** (common rules plus project rules),
   giving the reason for each decision. Similar tasks from earlier plans are shown as evidence.
3. **Audits**: required fields, duplicates by wording (within the batch and against Jira) and by meaning (against
   earlier plans, using embeddings), and estimates over the limits set in the rules.
4. Builds the **Jira hierarchy** (Epic → Story → Task → Subtask).
5. Waits for a **human to approve**. The approval is bound to the plan's hash, so any change needs a new approval.
6. **Pushes to Jira** through MCP, with resume after failure.
7. **Keeps Jira in sync with Excel**: when the Excel file changes, the agent shows what was added, changed or
   removed. Applying the changes updates the Jira issues that already exist instead of creating new ones.

Everything is stored in a database: runs, task versions, plan versions, approvals and the audit log. Earlier work
can be reopened, and the knowledge base (tasks, rules, documents) grows with every project.

## Users and value

| User | Value |
|---|---|
| PM / Delivery lead | A plan in minutes instead of hours; consistent categories and estimates; no duplicate issues |
| Team members | Clear tasks with acceptance criteria and the source in the document |
| QA / Auditor | Every decision has a reason and is logged (who approved what, when, and what changed) |

## Scope of workshop 3

The workshop 1 agent was copied, then made scalable and more useful: DB persistence (SQLite, can move to
PostgreSQL), ChromaDB vector lookup with the STU `text-embedding-3-small` model, typed function calling, retry and
batching for every model call, and Excel → task sync.
