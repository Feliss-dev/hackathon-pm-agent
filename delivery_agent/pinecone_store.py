"""Pinecone as a drop-in backend for `VectorStore` (VECTOR_BACKEND=pinecone).

`PineconeCollection` offers the few calls `VectorStore` makes on a Chroma collection (upsert / query / get / delete /
count), so the same business code runs on either store. One Pinecone index holds the three collections, one
namespace each (tasks, rules, docs); `project_key` stays a metadata filter. Pinecone keeps no document text, so the text
is stored in the `text` metadata field. Pinecone is eventually consistent: `eventual = True` tells `VectorStore` not to
trust `count()` right after a write.

`InMemoryPineconeIndex` has the same surface as a Pinecone `Index` for tests and offline demos.
"""
from __future__ import annotations

import math
import os
import time
from types import SimpleNamespace

UPSERT_BATCH = 100
TEXT_KEY = "text"


def _matches(meta: dict, flt: dict | None) -> bool:
    """Pinecone/Mongo-style filter: equality, $eq, $ne, $in, $and."""
    for key, cond in (flt or {}).items():
        if key == "$and":
            if not all(_matches(meta, c) for c in cond):
                return False
        elif isinstance(cond, dict):
            val = meta.get(key)
            for op, arg in cond.items():
                if (op == "$eq" and val != arg) or (op == "$ne" and val == arg) or (op == "$in" and val not in arg):
                    return False
        elif meta.get(key) != cond:
            return False
    return True


def _cos(a: list[float], b: list[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


class InMemoryPineconeIndex:
    def __init__(self):
        self.ns: dict[str, dict[str, dict]] = {}

    def upsert(self, vectors, namespace=""):
        for v in vectors:
            self.ns.setdefault(namespace, {})[v["id"]] = v

    def query(self, vector, top_k, namespace="", filter=None, include_metadata=True):
        rows = sorted(((_cos(vector, v["values"]), v) for v in self.ns.get(namespace, {}).values()
                       if _matches(v["metadata"], filter)), key=lambda x: -x[0])
        return SimpleNamespace(matches=[SimpleNamespace(id=v["id"], score=s, metadata=v["metadata"])
                                        for s, v in rows[:top_k]])

    def delete(self, ids=None, namespace="", delete_all=False):
        if delete_all:
            self.ns.pop(namespace, None)
        for i in ids or []:
            self.ns.get(namespace, {}).pop(i, None)

    def describe_index_stats(self):
        return SimpleNamespace(namespaces={n: SimpleNamespace(vector_count=len(v)) for n, v in self.ns.items()})


class PineconeCollection:
    eventual = True

    def __init__(self, index, namespace: str, dim: int):
        self.index, self.namespace, self.dim = index, namespace, dim

    def upsert(self, ids, documents, metadatas, embeddings) -> None:
        for s in range(0, len(ids), UPSERT_BATCH):
            self.index.upsert(vectors=[
                {"id": i, "values": [float(x) for x in e],
                 "metadata": {k: v for k, v in (m or {}).items() if v is not None} | {TEXT_KEY: d}}
                for i, d, m, e in zip(ids[s:s + UPSERT_BATCH], documents[s:s + UPSERT_BATCH],
                                      metadatas[s:s + UPSERT_BATCH], embeddings[s:s + UPSERT_BATCH])],
                namespace=self.namespace)

    def _run(self, vector, top_k, where):
        for attempt in range(3):          # a fresh index can answer 404 for a moment
            try:
                return self.index.query(vector=vector, top_k=top_k, namespace=self.namespace, filter=where or None,
                                        include_metadata=True).matches
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(2)

    def query(self, query_embeddings, n_results, where=None, include=None) -> dict:
        out = {"ids": [], "documents": [], "metadatas": [], "distances": []}
        for vec in query_embeddings:
            hits = self._run([float(x) for x in vec], n_results, where)
            out["ids"].append([h.id for h in hits])
            out["documents"].append([(h.metadata or {}).get(TEXT_KEY, "") for h in hits])
            out["metadatas"].append([{k: v for k, v in (h.metadata or {}).items() if k != TEXT_KEY} for h in hits])
            out["distances"].append([1 - h.score for h in hits])      # Chroma returns cosine distance
        return out

    def get(self, where=None, include=None) -> dict:
        hits = self._run([1.0] * self.dim, 1000, where)
        return {"ids": [h.id for h in hits],
                "metadatas": [{k: v for k, v in (h.metadata or {}).items() if k != TEXT_KEY} for h in hits]}

    def delete(self, ids) -> None:
        if ids:
            self.index.delete(ids=list(ids), namespace=self.namespace)

    def count(self) -> int:
        namespaces = self.index.describe_index_stats().namespaces
        summary = namespaces.get(self.namespace) if isinstance(namespaces, dict) else None
        return int(getattr(summary, "vector_count", 0) or 0)


def open_pinecone_index(dim: int, suffix: str):
    """The Pinecone serverless index for one embedding model (created on first use)."""
    api_key = os.getenv("PINECONE_API_KEY")
    if not api_key:
        raise RuntimeError("VECTOR_BACKEND=pinecone needs PINECONE_API_KEY in .env")
    from pinecone import Pinecone, ServerlessSpec
    pc = Pinecone(api_key=api_key)
    name = f"{os.getenv('PINECONE_INDEX', 'delivery-agent')}-{suffix}".lower()[:45].strip("-")
    if name not in [i.name for i in pc.list_indexes()]:
        pc.create_index(name, dimension=dim, metric="cosine",
                        spec=ServerlessSpec(cloud=os.getenv("PINECONE_CLOUD", "aws"),
                                            region=os.getenv("PINECONE_REGION", "us-east-1")))
        while not pc.describe_index(name).status["ready"]:
            time.sleep(2)
    return pc.Index(name)
