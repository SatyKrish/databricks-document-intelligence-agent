"""Unit tests for hybrid retrieval + re-rank.

Mocks the Vector Search and re-ranker SDK calls so tests don't hit the
workspace.
"""

from __future__ import annotations

import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest


os.environ.setdefault("DOCINTEL_CATALOG", "test_catalog")
os.environ.setdefault("DOCINTEL_SCHEMA", "test_schema")


@pytest.fixture
def fake_vs(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    fake_module = types.ModuleType("databricks.vector_search.client")
    client_class = MagicMock()
    fake_module.VectorSearchClient = client_class
    sys.modules["databricks.vector_search"] = types.ModuleType("databricks.vector_search")
    sys.modules["databricks.vector_search.client"] = fake_module
    yield client_class


def _candidates(n: int) -> list[list]:
    return [
        [f"sec-{i}", "AAPL_10K_2024.pdf", "Risk", "Risk Factors", f"summary {i}", 25, 0.9 - i * 0.05]
        for i in range(n)
    ]


def test_returns_top_k_after_rerank(fake_vs: MagicMock) -> None:
    instance = fake_vs.return_value
    instance.get_index.return_value.similarity_search.return_value = {"result": {"data_array": _candidates(25)}}

    with patch("agent.retrieval._rerank", return_value=list(range(5))) as rerank:
        from agent import retrieval

        citations, retrieved = retrieval.hybrid_retrieve("top risks?", top_k=5)

    assert len(citations) == 5
    assert retrieved == 25
    rerank.assert_called_once()


def test_empty_index_returns_empty(fake_vs: MagicMock) -> None:
    instance = fake_vs.return_value
    instance.get_index.return_value.similarity_search.return_value = {"result": {"data_array": []}}
    from agent import retrieval

    citations, retrieved = retrieval.hybrid_retrieve("nothing matches", top_k=5)
    assert citations == []
    assert retrieved == 0


def test_company_filter_passes_through(fake_vs: MagicMock) -> None:
    instance = fake_vs.return_value
    index = instance.get_index.return_value
    index.similarity_search.return_value = {"result": {"data_array": _candidates(3)}}
    from agent import retrieval

    retrieval.hybrid_retrieve("Apple risks", top_k=5, company_filter="AAPL")
    kwargs = index.similarity_search.call_args.kwargs
    assert kwargs.get("filters") and "filename LIKE" in next(iter(kwargs["filters"].keys()))
