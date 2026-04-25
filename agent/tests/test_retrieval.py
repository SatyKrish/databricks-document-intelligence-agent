"""Unit tests for hybrid retrieval + re-rank.

Mocks the VectorSearchClient already imported by `agent.retrieval` so tests
don't hit the workspace.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


os.environ.setdefault("DOCINTEL_CATALOG", "test_catalog")
os.environ.setdefault("DOCINTEL_SCHEMA", "test_schema")


def _candidates(n: int) -> list[list]:
    # Order of values matches retrieval._RETURN_COLS, with the trailing score column.
    return [
        [f"sec-{i}", "AAPL_10K_2024.pdf", "Risk", "Risk Factors", f"summary {i}", 25, 0.9 - i * 0.05]
        for i in range(n)
    ]


@pytest.fixture
def fake_index(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch VectorSearchClient on the already-loaded retrieval module."""
    from agent import retrieval

    client = MagicMock()
    index = MagicMock()
    client.return_value.get_index.return_value = index
    monkeypatch.setattr(retrieval, "VectorSearchClient", client)
    return index


def test_returns_top_k_after_rerank(fake_index: MagicMock) -> None:
    fake_index.similarity_search.return_value = {"result": {"data_array": _candidates(25)}}

    with patch("agent.retrieval._rerank", return_value=list(range(5))) as rerank:
        from agent import retrieval

        citations, retrieved = retrieval.hybrid_retrieve("top risks?", top_k=5)

    assert len(citations) == 5
    assert retrieved == 25
    rerank.assert_called_once()


def test_empty_index_returns_empty(fake_index: MagicMock) -> None:
    fake_index.similarity_search.return_value = {"result": {"data_array": []}}
    from agent import retrieval

    citations, retrieved = retrieval.hybrid_retrieve("nothing matches", top_k=5)
    assert citations == []
    assert retrieved == 0


def test_company_filter_passes_through(fake_index: MagicMock) -> None:
    fake_index.similarity_search.return_value = {"result": {"data_array": _candidates(3)}}
    from agent import retrieval

    retrieval.hybrid_retrieve("Apple risks", top_k=5, company_filter="Apple")
    kwargs = fake_index.similarity_search.call_args.kwargs
    assert kwargs.get("filters") == {"company_filter_text LIKE": "%Apple%"}


def test_company_and_year_filters_do_not_clobber(fake_index: MagicMock) -> None:
    fake_index.similarity_search.return_value = {"result": {"data_array": _candidates(3)}}
    from agent import retrieval

    retrieval.hybrid_retrieve("Apple FY2024 risks", top_k=5, company_filter="Apple", fiscal_year_filter=2024)
    kwargs = fake_index.similarity_search.call_args.kwargs
    assert kwargs.get("filters") == {"company_filter_text LIKE": "%Apple%", "fiscal_year =": 2024}


def test_rerank_failure_falls_back_to_vector_order(fake_index: MagicMock) -> None:
    fake_index.similarity_search.return_value = {"result": {"data_array": _candidates(8)}}
    from agent import retrieval

    with patch("agent.retrieval.WorkspaceClient") as workspace:
        workspace.return_value.serving_endpoints.query.side_effect = RuntimeError("missing endpoint")
        citations, retrieved = retrieval.hybrid_retrieve("top risks?", top_k=5)

    assert [c.snippet for c in citations] == [f"summary {i}" for i in range(5)]
    assert retrieved == 8
