"""Step 1 - task extraction from documents (Claude) and Excel task lists (deterministic)."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from . import excel_io
from .llm import DEFAULT_BASE_URL, DEFAULT_MODEL, LLMConfigError, chat_model, get_client, structured_completion, with_retry  # noqa: F401
from .models import Task
CLAUDE_MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """You are a delivery planning assistant. You read project documents (business requirement \
documents, design documents, workshop deliverables, migration assessment outputs) and turn them into an \
actionable task list for a software delivery team.

For each task:
- title: short imperative work item, e.g. "Create Customer API".
- description: what must be done, in 1-3 sentences, grounded in the document.
- acceptanceCriteria: concrete, testable criteria taken from or implied by the document; "" if none can be justified.
- assignee: only if the document names a person or role for it; otherwise "".
- dependency: titles of other tasks in YOUR list that must finish first.
- estimate: only if the document states an effort (e.g. "3d", "16h"); never invent one - leave "".
- story: the feature / user story / module the task belongs to, used to group tasks under Jira Stories.
- parentTitle: the title of another task in your list if this is a sub-step of it; otherwise "".
- category: leave "" - classification is done by deterministic rules later.

Split work so each task is something one person can finish within a sprint. Cover analysis, design, \
implementation, testing and migration work the document calls for, and do not add work the document does not support."""

_TASK_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "acceptanceCriteria": {"type": "string"},
        "assignee": {"type": "string"},
        "dependency": {"type": "array", "items": {"type": "string"}},
        "category": {"type": "string"},
        "estimate": {"type": "string"},
        "story": {"type": "string"},
        "parentTitle": {"type": "string"},
    },
    "required": ["title", "description", "acceptanceCriteria", "assignee", "dependency",
                 "category", "estimate", "story", "parentTitle"],
    "additionalProperties": False,
}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"tasks": {"type": "array", "items": _TASK_SCHEMA}},
    "required": ["tasks"],
    "additionalProperties": False,
}

TEXT_SUFFIXES = {".md", ".txt", ".csv", ".json", ".yaml", ".yml", ".html"}


class ExtractionError(RuntimeError):
    pass


def document_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader
        return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    if suffix == ".docx":
        import docx
        doc = docx.Document(str(path))
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                parts.append(" | ".join(c.text.strip() for c in row.cells))
        return "\n".join(parts)
    if suffix in TEXT_SUFFIXES:
        return path.read_text(encoding="utf-8", errors="replace")
    raise ExtractionError(f"Unsupported document type: {path.name}")


def _document_block(path: Path) -> dict:
    """Claude content block; PDFs are sent natively so Claude also sees tables and layout."""
    if path.suffix.lower() == ".pdf":
        data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
        return {"type": "document", "title": path.name,
                "source": {"type": "base64", "media_type": "application/pdf", "data": data}}
    return {"type": "document", "title": path.name,
            "source": {"type": "text", "media_type": "text/plain", "data": document_text(path)}}


def _parse_tasks(text: str, paths: list[Path]) -> list[Task]:
    try:
        raw = json.loads(text)["tasks"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ExtractionError(f"Could not parse model output: {exc}") from exc
    source = ", ".join(p.name for p in paths)
    return [Task(**t, id="", source=source) for t in raw]


def _extract_openai_compatible(paths: list[Path], client=None) -> list[Task]:
    """OpenAI-compatible gateway (LLM_BASE_URL / LLM_API_KEY / LLM_MODEL) via Chat Completions with `response_format`."""
    if client is None:
        try:
            client = get_client(timeout=300)
        except LLMConfigError as exc:
            raise ExtractionError("Set LLM_API_KEY (or API_KEY) in .env for the AI gateway") from exc
    docs = "\n\n".join(f"<document name=\"{p.name}\">\n{document_text(p)}\n</document>" for p in paths)
    response = structured_completion(
        client, system=SYSTEM_PROMPT, user=f"{docs}\n\nExtract the delivery task list from the documents above.",
        name="task_list", schema=OUTPUT_SCHEMA, what="task extraction")
    choice = response.choices[0]
    if getattr(choice, "finish_reason", "stop") == "length":
        raise ExtractionError("Model output incomplete: it hit the output token limit")
    return _parse_tasks(choice.message.content or "", paths)


def _extract_claude(paths: list[Path], client=None) -> list[Task]:
    import anthropic
    client = client or anthropic.Anthropic()
    content = [_document_block(p) for p in paths]
    content.append({"type": "text", "text": "Extract the delivery task list from the documents above."})

    with client.beta.messages.stream(
        model=CLAUDE_MODEL,
        max_tokens=64000,
        system=SYSTEM_PROMPT,
        thinking={"type": "adaptive"},
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": content}],
    ) as stream:
        message = stream.get_final_message()

    if message.stop_reason == "refusal":
        raise ExtractionError(f"Model declined the request: {getattr(message, 'stop_details', None)}")
    if message.stop_reason == "max_tokens":
        raise ExtractionError("Output hit max_tokens; split the documents into smaller batches")
    return _parse_tasks(next((b.text for b in message.content if b.type == "text"), ""), paths)


def extract_from_documents(paths: list[Path], client=None, provider: str | None = None) -> list[Task]:
    """Send all documents in one request and get a schema-validated task list back.

    LLM_PROVIDER=openai (default) uses the OpenAI-compatible gateway; LLM_PROVIDER=anthropic uses Claude.
    """
    if not paths:
        return []
    provider = (provider or os.getenv("LLM_PROVIDER", "openai")).lower()
    if provider == "anthropic":
        return _extract_claude(paths, client)
    if provider == "openai":
        return _extract_openai_compatible(paths, client)
    raise ExtractionError(f"Unknown LLM_PROVIDER '{provider}' (use openai or anthropic)")


def extract(inputs: list[str | Path], config: dict, client=None) -> list[Task]:
    """Route each input: spreadsheets are parsed directly, everything else goes to the LLM."""
    tasks: list[Task] = []
    documents: list[Path] = []
    for item in inputs:
        path = Path(item)
        if not path.exists():
            raise FileNotFoundError(path)
        if path.suffix.lower() in (".xlsx", ".xlsm"):
            tasks.extend(excel_io.read_tasks(path, config))
        else:
            documents.append(path)
    tasks.extend(extract_from_documents(documents, client=client))
    for n, task in enumerate(tasks, 1):
        task.id = task.id or f"T{n:03d}"
    return tasks
