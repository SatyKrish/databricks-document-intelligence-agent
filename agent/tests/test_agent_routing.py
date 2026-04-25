"""Routing tests for AnalystAgent: cross-company → supervisor, single → grounded path."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest


os.environ.setdefault("DOCINTEL_CATALOG", "test_catalog")
os.environ.setdefault("DOCINTEL_SCHEMA", "test_schema")
os.environ.setdefault("DOCINTEL_WAREHOUSE_ID", "test_warehouse")


def test_no_grounded_source_returns_canonical_message() -> None:
    from agent.analyst_agent import AnalystAgent, NO_SOURCE_MESSAGE

    with patch("agent.retrieval.hybrid_retrieve", return_value=([], 0)):
        out = AnalystAgent().predict(None, {"question": "Tell me about XYZ Corp's risks"})

    assert out["grounded"] is False
    assert out["citations"] == []
    assert out["answer"] == NO_SOURCE_MESSAGE


def test_cross_company_question_routes_to_supervisor() -> None:
    from agent.analyst_agent import AnalystAgent

    with patch("agent.supervisor.handle", return_value={"agent_path": "supervisor", "answer": "ok", "grounded": True, "citations": [], "latency_ms": 1, "retrieved_count": 0, "conversation_id": None, "turn_id": "t", "missing_companies": []}) as supervisor:
        out = AnalystAgent().predict(None, {"question": "Compare Apple and Microsoft revenue"})

    supervisor.assert_called_once()
    assert out["agent_path"] == "supervisor"


def test_single_company_question_uses_analyst_path(monkeypatch) -> None:
    from agent.analyst_agent import AnalystAgent
    from agent.retrieval import Citation

    citations = [Citation("AAPL_10K_2024.pdf", "Risk", 0.9, snippet="snippet")]

    with patch("agent.retrieval.hybrid_retrieve", return_value=(citations, 25)), \
         patch("agent.analyst_agent._generate", return_value="generated answer with [1]"):
        out = AnalystAgent().predict(None, {"question": "What are Apple's top risks?"})

    assert out["agent_path"] == "analyst"
    assert out["grounded"] is True
    assert len(out["citations"]) == 1
