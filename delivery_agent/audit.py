"""Append-only audit log - every decision the agent makes is written here.

Entries go to a JSONL file (downloadable) and, when a `sink` is given, to the database `audit_log` table."""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from .models import AuditEntry


class AuditLog:
    def __init__(self, path: str | Path | None = None, sink: Callable[[dict], None] | None = None):
        self.path = Path(path) if path else None
        self.sink = sink
        self.entries: list[AuditEntry] = []
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, step: str, **fields) -> AuditEntry:
        entry = AuditEntry(timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"), step=step, **fields)
        self.entries.append(entry)
        data = entry.model_dump(by_alias=True, exclude_defaults=True)
        if self.path:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")
        if self.sink:
            self.sink(data)
        return entry
