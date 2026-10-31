"""LangChain parts of the agent.

- `ProjectRetriever`   the project's vector search (`VectorStore.search_docs`) as a LangChain `BaseRetriever`
- `answer_chain`       LCEL chain `prompt | chat model | parser` that answers only from the retrieved passages and cites them
- `rag_chain`          `retriever | format | answer_chain` end to end
- `langchain_tools`    every tool of the typed registry as a LangChain `StructuredTool` (Pydantic args model = args_schema)
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.retrievers import BaseRetriever
from langchain_core.runnables import Runnable, RunnablePassthrough
from langchain_core.tools import StructuredTool

from .llm import DEFAULT_BASE_URL, LLMConfigError, chat_model

TOOL_RESULT_MAX = 8000

RAG_PROMPT = ChatPromptTemplate.from_template(
    "Answer ONLY from the passages below, in the language of the question, and cite every fact as [file#chunk]. "
    "If the passages do not contain the answer, say it is not in the documents.\n\n"
    "Passages:\n{context}\n\nQuestion: {question}")


class ProjectRetriever(BaseRetriever):
    """Top-k passages of the uploaded documents of one project (optionally one run)."""
    store: Any
    project_key: str
    run_id: str | None = None
    k: int = 5

    def _get_relevant_documents(self, query: str, *, run_manager=None) -> list[Document]:
        return [Document(page_content=h.text, metadata={**h.meta, "score": round(h.score, 3)})
                for h in self.store.search_docs(self.project_key, query, self.k, self.run_id)]


def format_docs(docs: list[Document]) -> str:
    return "\n\n".join(f"[{d.metadata.get('filename')}#{d.metadata.get('chunk')}]\n{d.page_content}" for d in docs)


def default_chat_model():
    """ChatOpenAI pointed at the same gateway / model / key as the rest of the app."""
    api_key = os.getenv("LLM_API_KEY") or os.getenv("API_KEY")
    if not api_key:
        raise LLMConfigError("Missing LLM_API_KEY (or API_KEY) in .env")
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=chat_model(), base_url=os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL), api_key=api_key,
                      temperature=0, max_retries=int(os.getenv("LLM_MAX_RETRIES", "4")), timeout=120)


def answer_chain(llm: Runnable) -> Runnable:
    """{"context": str, "question": str} -> grounded answer with citations."""
    return RAG_PROMPT | llm | StrOutputParser()


def rag_chain(retriever: BaseRetriever, llm: Runnable) -> Runnable:
    """question -> answer: retrieve, format the passages, answer from them."""
    return {"context": retriever | format_docs, "question": RunnablePassthrough()} | answer_chain(llm)


def _plain(value):
    """Nested Pydantic objects from LangChain's argument parsing back to plain JSON values."""
    return json.loads(json.dumps(value, default=lambda o: o.model_dump() if hasattr(o, "model_dump") else str(o)))


def langchain_tools(session, sink: Callable[[str, dict, dict], None] | None = None) -> dict[str, StructuredTool]:
    """The registry's tools as StructuredTools. Calling one validates the arguments (args_schema), runs the tool through
    `REGISTRY.call` (timing, audit, error-as-result) and returns the result as JSON text; `sink(name, args, result)` is
    told about every call."""
    from .tools import REGISTRY
    tools: dict[str, StructuredTool] = {}
    for name, t in REGISTRY.tools.items():
        def run(__name: str = name, **kwargs) -> str:
            args, result = REGISTRY.call(session, __name, _plain(kwargs))
            if sink:
                sink(__name, args, result)
            return json.dumps(result, ensure_ascii=False, default=str)[:TOOL_RESULT_MAX]
        tools[name] = StructuredTool.from_function(func=run, name=name, description=t.description, args_schema=t.args)
    return tools
