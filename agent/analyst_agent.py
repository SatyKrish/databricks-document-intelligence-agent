"""Mosaic AI Custom Analyst Agent for the 10-K corpus.

Routes single-filing questions to grounded retrieval + LLM generation,
delegates cross-company questions to supervisor.handle().
FR-007, FR-014 (no source -> "no grounded source found").
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from typing import Any

import mlflow
from databricks.sdk import WorkspaceClient

from agent import retrieval
from agent._obo import user_workspace
from agent.retrieval import Citation


FOUNDATION_MODEL = os.environ.get("DOCINTEL_FOUNDATION_ENDPOINT", "databricks-meta-llama-3-3-70b-instruct")
NO_SOURCE_MESSAGE = "No grounded source found for this question in the indexed 10-K corpus."

_COMPARE_TOKENS = (" vs ", " versus ", "compare", "between", "across")

# Capitalized tokens that aren't company names. Without this, "What are Apple's
# revenue and EBITDA?" mis-routes to the supervisor because EBITDA, What, etc.
# count as candidate companies.
_ROUTING_STOP_TOKENS = {
    "what", "which", "how", "why", "when", "where", "who",
    "the", "and", "but", "for", "with", "their", "most", "recent",
    "between", "across", "compare", "vs", "versus", "fy", "fiscal", "year",
    "ebitda", "revenue", "kpis", "kpi", "10-k", "10k", "form",
    "company", "companies", "corp", "inc", "ltd", "llc",
}


class AnalystAgent(mlflow.pyfunc.PythonModel):
    def predict(self, context: Any, model_input: Any) -> dict[str, Any]:
        request = _coerce_request(model_input)
        question = request["question"]
        top_k = int(request.get("top_k") or 5)

        if _is_cross_company(question):
            from agent import supervisor  # lazy import; avoids cycle for tests
            return supervisor.handle(question=question, top_k=top_k, conversation_id=request.get("conversation_id"))

        return _single_filing(
            question=question,
            top_k=top_k,
            company_filter=request.get("company_filter"),
            fiscal_year_filter=request.get("fiscal_year_filter"),
            conversation_id=request.get("conversation_id"),
        )


def _single_filing(
    *,
    question: str,
    top_k: int,
    company_filter: str | None,
    fiscal_year_filter: int | None,
    conversation_id: str | None,
) -> dict[str, Any]:
    started = time.monotonic()
    citations, retrieved_count = retrieval.hybrid_retrieve(
        question,
        top_k=top_k,
        company_filter=company_filter,
        fiscal_year_filter=fiscal_year_filter,
    )

    if not citations:
        return _response(
            answer=NO_SOURCE_MESSAGE,
            grounded=False,
            citations=[],
            retrieved_count=retrieved_count,
            agent_path="analyst",
            started=started,
            conversation_id=conversation_id,
        )

    answer = _generate(question=question, citations=citations)
    return _response(
        answer=answer,
        grounded=True,
        citations=citations,
        retrieved_count=retrieved_count,
        agent_path="analyst",
        started=started,
        conversation_id=conversation_id,
    )


def _generate(*, question: str, citations: list[Citation]) -> str:
    w = user_workspace()
    sources = "\n\n".join(
        f"[{i + 1}] {c.filename} — {c.section_label}\n{c.snippet}"
        for i, c in enumerate(citations)
    )
    prompt = (
        "You are an equity research assistant. Answer the analyst's question using ONLY the cited 10-K sections "
        "below. Cite sources inline as [1], [2], etc. matching the section index. If the sources don't answer the "
        f"question, reply '{NO_SOURCE_MESSAGE}'.\n\n"
        f"QUESTION:\n{question}\n\nSOURCES:\n{sources}"
    )
    out = w.serving_endpoints.query(
        name=FOUNDATION_MODEL,
        messages=[{"role": "user", "content": prompt}],
    )
    if hasattr(out, "choices") and out.choices:
        return out.choices[0].message.content
    return out["choices"][0]["message"]["content"]


def _response(
    *,
    answer: str,
    grounded: bool,
    citations: list[Citation],
    retrieved_count: int,
    agent_path: str,
    started: float,
    conversation_id: str | None,
) -> dict[str, Any]:
    return {
        "answer": answer,
        "grounded": grounded,
        "citations": [c.to_dict() for c in citations],
        "latency_ms": int((time.monotonic() - started) * 1000),
        "retrieved_count": retrieved_count,
        "agent_path": agent_path,
        "conversation_id": conversation_id,
        "turn_id": str(uuid.uuid4()),
    }


def _coerce_request(model_input: Any) -> dict[str, Any]:
    if hasattr(model_input, "to_dict"):
        rows = model_input.to_dict(orient="records")
        return rows[0] if rows else {}
    if isinstance(model_input, list) and model_input:
        return model_input[0] if isinstance(model_input[0], dict) else json.loads(model_input[0])
    if isinstance(model_input, dict):
        return model_input
    if isinstance(model_input, str):
        return json.loads(model_input)
    raise TypeError(f"Unsupported request type: {type(model_input)!r}")


def _is_cross_company(question: str) -> bool:
    """Return True only when the question is a comparison AND mentions ≥ 2 plausible
    company tokens. The capitalized-token heuristic strips question words, financial
    metric names, and form-name boilerplate so a single-company question like
    "What are Apple's revenue and EBITDA?" stays on the analyst path.
    """
    lowered = question.lower()
    if not any(token in lowered for token in _COMPARE_TOKENS):
        return False
    capitalized = re.findall(r"\b[A-Z][A-Za-z][A-Za-z0-9&\.\-]+\b", question)
    candidates = {w for w in capitalized if w.lower() not in _ROUTING_STOP_TOKENS}
    return len(candidates) >= 2
