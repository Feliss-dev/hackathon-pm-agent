"""One place for every model call: client, retry policy and embeddings.

- `get_client()`   OpenAI-compatible client for the STU AI gateway (LLM_BASE_URL / LLM_API_KEY).
- `with_retry()`   exponential backoff + jitter for rate limits, timeouts, connection and 5xx errors only;
                   a 400/401/422 is a bug or a bad key and is raised at once. Each retry is reported to `on_retry`.
- `Embedder`       batched embeddings (up to EMBED_BATCH inputs per request) with a content-hash cache, so the same
                   text is never embedded twice. EMBEDDING_PROVIDER=openai (STU gateway, default), hf
                   (local Hugging Face sentence-transformers model, no API key needed) or auto (gateway if a key exists).
"""
from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Callable
from typing import TypeVar

DEFAULT_BASE_URL = "https://aiportalapi.stu-platform.live/jpe"  # standard endpoint; /jpe/v2 is a semantic cache that replays answers and breaks the agent loop
DEFAULT_MODEL = "DeepSeek-V4.1-Flash"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_HF_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"   # 50+ languages incl. Vietnamese, 384-d
EMBED_BATCH = 100

log = logging.getLogger(__name__)
T = TypeVar("T")


class LLMConfigError(RuntimeError):
    pass


def get_client(timeout: float = 120, base_url: str | None = None, api_key: str | None = None):
    """OpenAI-compatible client. SDK retries are off: `with_retry` owns the policy so retries are visible."""
    from openai import OpenAI
    api_key = api_key or os.getenv("LLM_API_KEY") or os.getenv("API_KEY")
    if not api_key:
        raise LLMConfigError("Missing LLM_API_KEY (or API_KEY) in .env")
    return OpenAI(base_url=base_url or os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL), api_key=api_key,
                  timeout=timeout, max_retries=0)


def chat_model() -> str:
    return os.getenv("LLM_MODEL", DEFAULT_MODEL)


def is_retryable(exc: BaseException) -> bool:
    import openai
    if isinstance(exc, (openai.RateLimitError, openai.APITimeoutError, openai.APIConnectionError)):
        return True
    return isinstance(exc, openai.APIStatusError) and exc.status_code >= 500


def with_retry(fn: Callable[[], T], *, what: str = "LLM call", on_retry: Callable[[str], None] | None = None,
               attempts: int | None = None, wait_max: float = 30) -> T:
    """Run `fn()` and retry transient API errors with exponential backoff + jitter."""
    from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_random_exponential

    attempts = attempts or int(os.getenv("LLM_MAX_RETRIES", "4")) + 1

    def before_sleep(state):
        msg = (f"{what}: attempt {state.attempt_number} failed ({type(state.outcome.exception()).__name__}); "
               f"retrying in {state.next_action.sleep:.1f}s")
        log.warning(msg)
        if on_retry:
            on_retry(msg)

    retrying = Retrying(retry=retry_if_exception(is_retryable), stop=stop_after_attempt(attempts),
                        wait=wait_random_exponential(multiplier=1, max=wait_max), before_sleep=before_sleep,
                        reraise=True)
    return retrying(fn)


def structured_completion(client, *, system: str, user: str, name: str, schema: dict, what: str,
                          on_retry: Callable[[str], None] | None = None, temperature: float | None = None):
    """Chat Completions call whose answer must match `schema`. Returns the raw response.

    The gateway honours `response_format` here but ignores `text.format` of the Responses API (it answers in prose),
    so every structured call goes through this helper.
    """
    kwargs = {"model": chat_model(),
              "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
              "response_format": {"type": "json_schema", "json_schema": {"name": name, "schema": schema, "strict": True}}}
    if temperature is not None:
        kwargs["temperature"] = temperature
    return with_retry(lambda: client.chat.completions.create(**kwargs), what=what, on_retry=on_retry)


def text_hash(text: str, model: str = "") -> str:
    return hashlib.sha256(f"{model}\x00{text}".encode("utf-8")).hexdigest()


class Embedder:
    """Turns texts into vectors in batches. `cache` is any object with get_many(hashes) / put_many(dict)."""

    def __init__(self, provider: str | None = None, model: str | None = None, client=None, cache=None,
                 on_retry: Callable[[str], None] | None = None):
        self.provider = (provider or os.getenv("EMBEDDING_PROVIDER", "openai")).lower()
        if self.provider == "auto":   # the gateway when a key exists, else the local Hugging Face model
            self.provider = "openai" if (os.getenv("EMBEDDING_API_KEY") or os.getenv("LLM_API_KEY") or os.getenv("API_KEY")) else "hf"
        default = DEFAULT_HF_MODEL if self.provider == "hf" else DEFAULT_EMBEDDING_MODEL
        env_model = os.getenv("HF_EMBEDDING_MODEL") or os.getenv("EMBEDDING_MODEL", "")
        if self.provider == "hf" and "/" not in env_model:     # EMBEDDING_MODEL holds an OpenAI model name
            env_model = ""
        self.model = model or env_model or default
        self._client = client
        self._hf = None
        self.cache = cache
        self.on_retry = on_retry
        self.requests = 0          # embedding API requests sent (for tests / stats)

    @property
    def client(self):
        if self._client is None:
            self._client = get_client(base_url=os.getenv("EMBEDDING_BASE_URL") or None,
                                      api_key=os.getenv("EMBEDDING_API_KEY") or None)
        return self._client

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.requests += 1
        if self.provider == "hf":
            if self._hf is None:
                from sentence_transformers import SentenceTransformer
                self._hf = SentenceTransformer(self.model)
            return [list(map(float, v)) for v in self._hf.encode(texts, normalize_embeddings=True)]
        if self.provider != "openai":
            raise LLMConfigError(f"Unknown EMBEDDING_PROVIDER '{self.provider}' (use openai, hf or auto)")
        response = with_retry(lambda: self.client.embeddings.create(model=self.model, input=texts),
                              what="embeddings", on_retry=self.on_retry)
        return [d.embedding for d in sorted(response.data, key=lambda d: d.index)]

    def embed(self, texts: list[str]) -> list[list[float]]:
        texts = [t if t.strip() else " " for t in texts]
        keys = [text_hash(t, self.model) for t in texts]
        found: dict[str, list[float]] = dict(self.cache.get_many(keys)) if self.cache else {}
        todo = list(dict.fromkeys(k for k in keys if k not in found))
        by_key = {k: t for k, t in zip(keys, texts)}
        new: dict[str, list[float]] = {}
        for start in range(0, len(todo), EMBED_BATCH):
            batch = todo[start:start + EMBED_BATCH]
            new.update(zip(batch, self._embed_batch([by_key[k] for k in batch])))
        if new and self.cache:
            self.cache.put_many(new)
        found.update(new)
        return [found[k] for k in keys]
