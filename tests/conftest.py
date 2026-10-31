import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "samples"))

from delivery_agent.ai_rules import RuleAssessor, RuleReading  # noqa: E402
from delivery_agent.config import load_config  # noqa: E402


class FakeAssessor(RuleAssessor):
    """Stands in for the AI's reading of the rules; the estimate comparison is the real code path."""

    RULES = [  # (pattern, category, max_hours, default_hours) - first match wins
        (r"test ?cases?", "Create Test Cases", None, 8),
        (r"\b(execute|run)\b.*\btest", "Execute Test", None, 8),
        (r"unit ?tests?", "UT", None, 8),
        (r"\breview\b", "Review Code", 16, 4),
        (r"migrat|\betl\b", "Migration", None, 24),
        (r"backfill", "Backfill", None, 8),
        (r"requirement", "BA", None, 16),
        (r"architecture", "SA", None, 16),
        (r"screen|\bui\b|\bform\b", "FE", None, 16),
        (r"\bapi\b|service", "BE", None, 16),
        (r"email|reply", "Communication", 8, 2),
        (r"meeting", "Management", 4, 4),
        (r"document|guide", "Document", 24, 8),
    ]

    def __init__(self):
        super().__init__("fake rules", client=object())
        self.calls = 0

    def read_rules(self, tasks):
        self.calls += 1
        out = {}
        for t in tasks:
            text = f"{t.title} {t.description}".lower()
            for pattern, category, max_h, default_h in self.RULES:
                if re.search(pattern, text):
                    out[t.id] = RuleReading(category, f"matched '{pattern}'", max_h, default_h, f"{category} rule")
                    break
            else:
                out[t.id] = RuleReading("Other", "no rule", None, 8, "default")
        return out


@pytest.fixture
def config():
    return load_config()


@pytest.fixture
def assessor():
    return FakeAssessor()


@pytest.fixture
def workbook(tmp_path):
    from make_samples import make_workbook
    path = tmp_path / "tracking.xlsx"
    make_workbook(path)
    return path


class FakeEmbedder:
    """Deterministic bag-of-words vectors: same words -> same direction. No network."""
    model = "fake-bow"

    def __init__(self, dim: int = 256):
        self.dim, self.requests = dim, 0

    def embed(self, texts):
        import hashlib
        self.requests += 1
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for w in re.findall(r"[a-z0-9]+", t.lower()):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1.0
            out.append(v)
        return out


@pytest.fixture
def repo():
    from delivery_agent.db import Repository
    return Repository("sqlite://")


@pytest.fixture
def vectors(tmp_path):
    import chromadb
    from delivery_agent.vectors import VectorStore
    return VectorStore(FakeEmbedder(), client=chromadb.PersistentClient(path=str(tmp_path / "chroma")))


@pytest.fixture
def make_session(config, repo, vectors, assessor, tmp_path):
    from delivery_agent.agent import AgentSession

    def make(**kw):
        kw = {"offline": True, "assessor": assessor, "repo": repo, "vectors": vectors, "out_root": tmp_path / "runs"} | kw
        return AgentSession(config, **kw)
    return make
