"""Pinecone vector backend (in-memory index), local text-to-speech helpers and embedding provider selection."""
import numpy as np
import pytest

from conftest import FakeEmbedder
from delivery_agent import speech
from delivery_agent.llm import DEFAULT_HF_MODEL, Embedder
from delivery_agent.models import Task
from delivery_agent.pinecone_store import InMemoryPineconeIndex
from delivery_agent.vectors import VectorStore


@pytest.fixture
def pc_store():
    return VectorStore(FakeEmbedder(), backend="pinecone", pinecone_index=InMemoryPineconeIndex())


def test_tasks_filter_by_project_and_run(pc_store):
    pc_store.index_tasks("KAN", "r1", [Task(id="A", title="Create Customer API", category="BE", estimate="24h"),
                                       Task(id="B", title="Weekly sync meeting", category="Management", estimate="2h")])
    pc_store.index_tasks("OTHER", "r9", [Task(id="Z", title="Create Customer API elsewhere")])
    hits = pc_store.similar_tasks("KAN", ["customer api"], k=5)[0]
    assert [h.meta["task_id"] for h in hits][0] == "A" and all(h.meta["project_key"] == "KAN" for h in hits)
    assert 0.0 <= hits[0].score <= 1.0 and hits[0].meta["estimate"] == "24h"
    assert pc_store.similar_tasks("KAN", ["customer api"], exclude_run="r1")[0] == []
    assert [h.meta["task_id"] for h in pc_store.similar_tasks("KAN", ["customer api"], only_run="r1")[0]][0] == "A"
    pc_store.remove_tasks("r1", ["A"])
    assert all(h.meta["task_id"] != "A" for h in pc_store.similar_tasks("KAN", ["customer api"], k=5)[0])


def test_docs_rules_and_stats(pc_store):
    n = pc_store.index_document("KAN", "r1", "brd.md", "# Auth\nUsers log in with OTP.\n\n# Pay\nVNPay checkout.")
    assert n >= 1
    hit = pc_store.search_docs("KAN", "otp login", k=1)[0]
    assert hit.meta["filename"] == "brd.md" and "OTP" in hit.text            # text round-trips through metadata

    rules = "# Rules\n1. Payment tasks are BE.\n2. Unit tests are UT.\n3. Meetings are Management."
    first = pc_store.index_rules("KAN", rules)
    assert pc_store.index_rules("KAN", rules) == first                          # unchanged text: no re-index
    picked = pc_store.relevant_rules("KAN", ["payment unit tests"], k_per_text=3)
    assert picked == sorted(picked, key=lambda r: rules.index(r.split("\n")[-1].strip()[:10]))   # original order kept
    assert pc_store.stats()["docs"] == n


def test_backend_selection_errors(monkeypatch):
    monkeypatch.delenv("PINECONE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="PINECONE_API_KEY"):
        VectorStore(FakeEmbedder(), backend="pinecone")
    with pytest.raises(ValueError, match="VECTOR_BACKEND"):
        VectorStore(FakeEmbedder(), backend="faiss")


def test_viet_numbers_and_summary_text():
    said = {0: "không", 5: "năm", 10: "mười", 15: "mười lăm", 21: "hai mươi mốt", 25: "hai mươi lăm", 101: "một trăm lẻ một",
            215: "hai trăm mười lăm", 1005: "một nghìn không trăm lẻ năm"}
    assert all(speech.viet_number(n) == w for n, w in said.items())
    plan = {"summary": {"counts": {"tasks_extracted": 6, "to_create": 7, "skipped": 1,
                                   "estimation_warnings": 3, "possible_duplicates": 0}}}
    vi = speech.summary_text(plan, "facebook/mms-tts-vie")
    assert "sáu nhiệm vụ" in vi and "bảy issue" in vi and not any(ch.isdigit() for ch in vi)
    assert "6 tasks" in speech.summary_text(plan, "facebook/mms-tts-eng")


def test_synthesize_writes_a_wav_and_session_offers_it(make_session, workbook, tmp_path, monkeypatch):
    monkeypatch.setitem(speech._pipelines, speech.DEFAULT_TTS_MODEL,
                        lambda text: {"audio": np.zeros((1, 16000), dtype=np.float32), "sampling_rate": 16000})
    info = speech.synthesize("xin chào", tmp_path / "a.wav")
    assert info["path"].exists() and info["seconds"] == 1.0

    s = make_session()
    s.analyze([str(workbook)])
    out = s.speak_plan()
    assert out["audio_file"] == "plan_summary.wav" and s.downloads["plan_summary.wav"].exists()
    assert "nhiệm vụ" in out["text"]


def test_embedding_provider_and_model_resolution(monkeypatch):
    for k in ("EMBEDDING_API_KEY", "LLM_API_KEY", "API_KEY", "HF_EMBEDDING_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-small")
    assert Embedder(provider="hf").model == DEFAULT_HF_MODEL                  # an OpenAI model name is not used for hf
    assert Embedder(provider="openai").model == "text-embedding-3-small"
    assert Embedder(provider="auto").provider == "hf"                         # no key anywhere
    monkeypatch.setenv("LLM_API_KEY", "x")
    assert Embedder(provider="auto").provider == "openai"
    monkeypatch.setenv("EMBEDDING_MODEL", "org/custom-model")
    assert Embedder(provider="hf").model == "org/custom-model"
