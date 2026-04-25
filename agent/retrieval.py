"""Hybrid retrieval + re-rank for the 10-K Analyst.

Top-25 hybrid (keyword + semantic) → Mosaic re-ranker → top-k. FR-007, SC-009.
Honors `embed_eligible` filter implicitly because the Vector Search index source view
already filters on it (see resources/vector_search/filings_index.yml +
pipelines/sql/04_gold_quality.sql).
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.vector_search.client import CredentialStrategy, VectorSearchClient

from agent._obo import user_workspace


CATALOG = os.environ["DOCINTEL_CATALOG"]
SCHEMA = os.environ["DOCINTEL_SCHEMA"]
INDEX_FQN = f"{CATALOG}.{SCHEMA}.filings_summary_idx"
ENDPOINT = os.environ.get("DOCINTEL_VS_ENDPOINT", f"docintel-{os.environ.get('DOCINTEL_TARGET', 'dev')}")
RERANK_ENDPOINT = os.environ.get("DOCINTEL_RERANK_ENDPOINT", "databricks-bge-rerank-v2")

_RETURN_COLS = ["section_uid", "filename", "section_label", "original_label", "summary", "quality_score"]
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Citation:
    filename: str
    section_label: str
    score: float
    snippet: str | None = None
    char_offset: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "section_label": self.section_label,
            "score": round(float(self.score), 4),
            "snippet": self.snippet,
            "char_offset": self.char_offset,
        }


def _filters(company: str | None, fiscal_year: int | None) -> dict[str, Any] | None:
    out: dict[str, Any] = {}
    if company:
        out["company_filter_text LIKE"] = f"%{company}%"
    if fiscal_year is not None:
        out["fiscal_year ="] = fiscal_year
    return out or None


def hybrid_retrieve(
    question: str,
    *,
    top_k: int = 5,
    company_filter: str | None = None,
    fiscal_year_filter: int | None = None,
    candidate_window: int = 25,
) -> tuple[list[Citation], int]:
    """Pull `candidate_window` hybrid candidates, re-rank to `top_k`. Returns (citations, retrieved_count)."""

    # VS user-scope: per Databricks Model Serving OBO docs, pass
    # CredentialStrategy.MODEL_SERVING_USER_CREDENTIALS rather than extracting
    # the token manually. The strategy resolves the per-request user context
    # the same way ModelServingUserCredentials does for WorkspaceClient.
    try:
        vsc = VectorSearchClient(
            credential_strategy=CredentialStrategy.MODEL_SERVING_USER_CREDENTIALS,
            disable_notice=True,
        )
    except Exception:
        # Outside Model Serving (tests, local dev) or OBO disabled — SP fallback.
        vsc = VectorSearchClient(disable_notice=True)
    index = vsc.get_index(endpoint_name=ENDPOINT, index_name=INDEX_FQN)
    raw = index.similarity_search(
        query_text=question,
        columns=_RETURN_COLS,
        num_results=candidate_window,
        query_type="HYBRID",
        filters=_filters(company_filter, fiscal_year_filter),
    )
    rows = raw.get("result", {}).get("data_array", [])
    if not rows:
        return [], 0

    documents = [{"text": _row(row, "summary"), "id": _row(row, "section_uid")} for row in rows]
    if len(documents) > top_k:
        order = _rerank(question, documents, top_k=top_k)
        rows = [rows[i] for i in order]
    else:
        rows = rows[:top_k]

    citations = [
        Citation(
            filename=_row(r, "filename"),
            section_label=_row(r, "section_label"),
            score=float(r[-1]),
            snippet=_truncate(_row(r, "summary"), 240),
        )
        for r in rows
    ]
    return citations, len(documents)


def _rerank(question: str, documents: list[dict[str, str]], *, top_k: int) -> list[int]:
    """Calls the Mosaic re-ranker endpoint; returns the original-row indices ordered by relevance."""
    w = user_workspace()
    try:
        response = w.serving_endpoints.query(
            name=RERANK_ENDPOINT,
            inputs={"query": question, "documents": [d["text"] for d in documents], "top_n": top_k},
        )
        ranked = response.predictions if hasattr(response, "predictions") else response["predictions"]
        return [item["index"] for item in ranked[:top_k]]
    except Exception as exc:  # pragma: no cover - workspace failure path
        _LOG.warning("Rerank endpoint %s failed; falling back to vector-search order: %s", RERANK_ENDPOINT, exc)
        return list(range(min(top_k, len(documents))))


def _row(row: list[Any], col: str) -> Any:
    return row[_RETURN_COLS.index(col)]


def _truncate(text: str | None, n: int) -> str | None:
    if not text:
        return None
    return text if len(text) <= n else text[: n - 1] + "…"
