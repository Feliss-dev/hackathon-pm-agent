"""Vector lookup with ChromaDB (default) or Pinecone (VECTOR_BACKEND=pinecone, see pinecone_store.py). Embeddings come
from `llm.Embedder` (STU text-embedding-3-small by default, or a local Hugging Face model), so the store never
downloads its own model.

Collections (every record carries `project_key`, so one store serves many projects):
  tasks  every planned task (title + description) with its final category / estimate / Jira key
         -> semantic duplicates against earlier runs, "similar past tasks" as estimate evidence, Excel sync matching
  rules  the human-written rules split into sections -> only the relevant rules are sent when the rules grow large
  docs   uploaded requirement documents split into chunks -> question answering with citations, task source passages
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .estimate import parse_hours
from .llm import Embedder
from .models import Task

ROOT = Path(__file__).resolve().parent.parent
CHUNK_CHARS = 2400          # ~600-800 tokens
CHUNK_OVERLAP = 300


@dataclass
class Hit:
    id: str
    text: str
    score: float            # cosine similarity, 1.0 = identical
    meta: dict


def chunk_markdown(text: str, max_chars: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split on headings first, then on paragraphs; long paragraphs are cut with overlap.
    Each chunk keeps its nearest heading so it still makes sense on its own."""
    sections, heading, buf = [], "", []
    for line in text.splitlines():
        if re.match(r"^#{1,6}\s", line):
            if any(x.strip() for x in buf):
                sections.append((heading, "\n".join(buf).strip()))
            heading, buf = line.strip(), []
        else:
            buf.append(line)
    if any(x.strip() for x in buf):
        sections.append((heading, "\n".join(buf).strip()))

    chunks: list[str] = []
    for heading, body in sections:
        prefix = f"{heading}\n" if heading else ""
        current = ""
        for para in re.split(r"\n\s*\n", body):
            para = para.strip()
            if not para:
                continue
            while len(para) > max_chars:                       # very long paragraph
                cut = para.rfind(" ", 0, max_chars) if " " in para[:max_chars] else max_chars
                chunks.append(prefix + para[:cut].strip())
                para = para[max(cut - overlap, 1):].strip()
            if current and len(current) + len(para) + 2 > max_chars:
                chunks.append(prefix + current)
                current = current[-overlap:].split(" ", 1)[-1] + "\n\n" + para if overlap else para
            else:
                current = f"{current}\n\n{para}" if current else para
        if current:
            chunks.append(prefix + current)
    return chunks


def split_rules(rules_text: str) -> list[str]:
    """Rules are split per section AND per numbered/bulleted rule so retrieval can pick single rules."""
    out, heading = [], ""
    for block in chunk_markdown(rules_text, max_chars=900, overlap=0):
        lines = block.splitlines()
        if lines and lines[0].startswith("#"):
            heading, lines = lines[0], lines[1:]
        items, cur = [], []
        for line in lines:
            if re.match(r"^\s*(\d+\.|[-*])\s", line) and cur:
                items.append("\n".join(cur))
                cur = []
            cur.append(line)
        if cur:
            items.append("\n".join(cur))
        out.extend(f"{heading}\n{i.strip()}" if heading else i.strip() for i in items if i.strip())
    return out


def task_text(t: Task) -> str:
    return f"{t.title}\n{t.description}".strip()


class VectorStore:
    def __init__(self, embedder: Embedder | None = None, path: str | Path | None = None, client=None,
                 backend: str | None = None, pinecone_index=None):
        self.embedder = embedder or Embedder()
        self.backend = (backend or os.getenv("VECTOR_BACKEND", "chroma")).lower()
        # One collection set per embedding model: vectors of different models are not comparable.
        suffix = re.sub(r"[^a-zA-Z0-9]+", "-", self.embedder.model).strip("-")[-40:]
        if self.backend == "pinecone":
            from .pinecone_store import PineconeCollection, open_pinecone_index
            dim = len(self.embedder.embed(["dimension probe"])[0])
            index = pinecone_index or open_pinecone_index(dim, suffix)
            self.client = index
            self._cols = {name: PineconeCollection(index, name, dim) for name in ("tasks", "rules", "docs")}
            return
        if self.backend != "chroma":
            raise ValueError(f"Unknown VECTOR_BACKEND '{self.backend}' (use chroma or pinecone)")
        import chromadb
        if client is None:
            path = Path(path or os.getenv("CHROMA_DIR", "data/chroma"))
            if not path.is_absolute():
                path = ROOT / path
            path.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(path=str(path))
        self.client = client
        self._cols = {name: client.get_or_create_collection(
            f"{name}-{suffix}", embedding_function=None, metadata={"hnsw:space": "cosine"})
            for name in ("tasks", "rules", "docs")}

    def col(self, name: str):
        return self._cols[name]

    # ---------------------------------------------------------------- generic
    def _upsert(self, name: str, ids: list[str], texts: list[str], metas: list[dict]) -> None:
        if not ids:
            return
        self.col(name).upsert(ids=ids, documents=texts, metadatas=metas, embeddings=self.embedder.embed(texts))

    def _query(self, name: str, texts: list[str], k: int, where: dict | None) -> list[list[Hit]]:
        col = self.col(name)
        eventual = getattr(col, "eventual", False)       # Pinecone: count() lags behind writes, so do not trust it
        if not texts or (not eventual and col.count() == 0):
            return [[] for _ in texts]
        res = col.query(query_embeddings=self.embedder.embed(texts), n_results=k if eventual else min(k, col.count()),
                        where=where, include=["documents", "metadatas", "distances"])
        return [[Hit(i, d, 1 - dist, m or {}) for i, d, m, dist in zip(ids, docs, metas, dists)]
                for ids, docs, metas, dists in zip(res["ids"], res["documents"], res["metadatas"], res["distances"])]

    @staticmethod
    def _where(**conds) -> dict | None:
        parts = [{k: v} for k, v in conds.items() if v is not None]
        if not parts:
            return None
        return parts[0] if len(parts) == 1 else {"$and": parts}

    # ---------------------------------------------------------------- tasks
    def index_tasks(self, project_key: str, run_id: str, tasks: list[Task], hours_per_day: float = 8) -> None:
        ids, texts, metas = [], [], []
        for t in tasks:
            hours = parse_hours(t.estimate, hours_per_day)
            ids.append(f"{run_id}:{t.id}")
            texts.append(task_text(t))
            metas.append({"project_key": project_key, "run_id": run_id, "task_id": t.id, "title": t.title,
                          "category": t.category or "", "estimate": t.estimate or "",
                          "estimate_hours": float(hours) if hours is not None else -1.0,
                          "jira_key": t.jira_key or ""})
        self._upsert("tasks", ids, texts, metas)

    def remove_tasks(self, run_id: str, task_ids: list[str]) -> None:
        if task_ids:
            self.col("tasks").delete(ids=[f"{run_id}:{t}" for t in task_ids])

    def similar_tasks(self, project_key: str, texts: list[str], k: int = 3, exclude_run: str | None = None,
                      only_run: str | None = None) -> list[list[Hit]]:
        """Nearest stored tasks for each text (one batched embedding call for all texts)."""
        where = self._where(project_key=project_key,
                            run_id={"$ne": exclude_run} if exclude_run else (only_run or None))
        return self._query("tasks", texts, k, where)

    # ---------------------------------------------------------------- rules
    def index_rules(self, project_key: str, rules_text: str) -> int:
        """(Re)index the rules of a profile; no-op when the text did not change. Returns the chunk count."""
        rules_hash = hashlib.sha256(rules_text.encode("utf-8")).hexdigest()[:16]
        col = self.col("rules")
        current = col.get(where={"project_key": project_key}, include=["metadatas"])
        if current["ids"] and all((m or {}).get("rules_hash") == rules_hash for m in current["metadatas"]):
            return len(current["ids"])
        if current["ids"]:
            col.delete(ids=current["ids"])
        chunks = split_rules(rules_text)
        self._upsert("rules", [f"{project_key}:{rules_hash}:{i}" for i in range(len(chunks))], chunks,
                     [{"project_key": project_key, "rules_hash": rules_hash, "order": i} for i in range(len(chunks))])
        return len(chunks)

    def relevant_rules(self, project_key: str, texts: list[str], k_per_text: int = 4) -> list[str]:
        """Union of the top rules for each text, returned in the ORIGINAL rule order (priority order matters)."""
        hits = self._query("rules", texts, k_per_text, {"project_key": project_key})
        chosen = {h.id: h for row in hits for h in row}
        return [h.text for h in sorted(chosen.values(), key=lambda h: h.meta.get("order", 0))]

    # ---------------------------------------------------------------- documents
    def index_document(self, project_key: str, run_id: str, filename: str, text: str) -> int:
        chunks = chunk_markdown(text)
        self._upsert("docs", [f"{run_id}:{filename}:{i}" for i in range(len(chunks))], chunks,
                     [{"project_key": project_key, "run_id": run_id, "filename": filename, "chunk": i}
                      for i in range(len(chunks))])
        return len(chunks)

    def search_docs(self, project_key: str, question: str, k: int = 5, run_id: str | None = None) -> list[Hit]:
        return self._query("docs", [question], k, self._where(project_key=project_key, run_id=run_id))[0]

    def stats(self) -> dict:
        return {name: col.count() for name, col in self._cols.items()}


_default: VectorStore | None = None


def get_vector_store() -> VectorStore:
    global _default
    if _default is None:
        from .db import get_repository
        _default = VectorStore(Embedder(cache=get_repository().cache("embedding")))
    return _default
