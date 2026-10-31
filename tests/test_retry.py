"""Retry with backoff for transient API errors; batched + cached embeddings."""
from types import SimpleNamespace

import httpx
import openai
import pytest

from delivery_agent.llm import EMBED_BATCH, Embedder, with_retry


def _err(cls, status):
    request = httpx.Request("POST", "https://gateway.test/v1")
    return cls("boom", response=httpx.Response(status, request=request), body=None)


def test_rate_limit_is_retried_then_succeeds():
    calls, notes = [], []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise _err(openai.RateLimitError, 429)
        return "ok"

    assert with_retry(flaky, on_retry=notes.append, wait_max=0.01) == "ok"
    assert len(calls) == 3 and len(notes) == 2 and "RateLimitError" in notes[0]


def test_server_error_retried_but_gives_up():
    calls = []

    def down():
        calls.append(1)
        raise _err(openai.InternalServerError, 503)

    with pytest.raises(openai.InternalServerError):
        with_retry(down, attempts=3, wait_max=0.01)
    assert len(calls) == 3


def test_client_errors_are_not_retried():
    calls = []

    def bad_key():
        calls.append(1)
        raise _err(openai.AuthenticationError, 401)

    with pytest.raises(openai.AuthenticationError):
        with_retry(bad_key, wait_max=0.01)
    assert len(calls) == 1


class FakeEmbeddingsClient:
    def __init__(self):
        self.requests = []
        self.embeddings = SimpleNamespace(create=self.create)

    def create(self, model, input):
        self.requests.append(len(input))
        data = [SimpleNamespace(index=i, embedding=[float(len(t)), 1.0]) for i, t in enumerate(input)]
        return SimpleNamespace(data=list(reversed(data)))      # out of order on purpose


def test_embeddings_are_batched_and_cached(repo):
    client = FakeEmbeddingsClient()
    emb = Embedder(provider="openai", model="m", client=client, cache=repo.cache("embedding"))
    texts = [f"task {i}" for i in range(EMBED_BATCH * 2 + 50)]
    vectors = emb.embed(texts)
    assert client.requests == [EMBED_BATCH, EMBED_BATCH, 50]
    assert vectors[12] == [float(len("task 12")), 1.0]           # order restored by index
    assert emb.embed(texts[:10] + ["new text"]) [-1] == [8.0, 1.0]
    assert client.requests[-1] == 1                                # only the new text was sent
