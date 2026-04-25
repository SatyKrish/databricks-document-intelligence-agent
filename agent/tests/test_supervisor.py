"""Tests for the supervisor (US3 cross-company)."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest


os.environ.setdefault("DOCINTEL_CATALOG", "test_catalog")
os.environ.setdefault("DOCINTEL_SCHEMA", "test_schema")
os.environ.setdefault("DOCINTEL_WAREHOUSE_ID", "test_warehouse")


@pytest.fixture
def fake_kpis() -> list[dict]:
    return [
        {
            "filename": "AAPL_10K_2024.pdf",
            "company_name": "Apple",
            "fiscal_year": 2024,
            "revenue": 391_000_000_000,
            "ebitda": 130_000_000_000,
            "segment_revenue": [{"name": "iPhone", "revenue": 200_000_000_000}, {"name": "Services", "revenue": 96_000_000_000}],
        },
        {
            "filename": "MSFT_10K_2024.pdf",
            "company_name": "Microsoft",
            "fiscal_year": 2024,
            "revenue": 245_000_000_000,
            "ebitda": 130_000_000_000,
            "segment_revenue": [{"name": "Azure", "revenue": 105_000_000_000}],
        },
    ]


def test_supervisor_returns_markdown_table(fake_kpis: list[dict]) -> None:
    from agent import supervisor

    with patch("agent.supervisor.tools.fetch_kpis_for_companies", return_value=fake_kpis), \
         patch("agent.supervisor.retrieval.hybrid_retrieve", return_value=([], 0)):
        out = supervisor.handle(question="Compare Apple and Microsoft revenue", top_k=5, conversation_id=None)

    assert out["agent_path"] == "supervisor"
    assert out["grounded"] is True
    assert "| Company |" in out["answer"]
    assert "Apple" in out["answer"] and "Microsoft" in out["answer"]


def test_supervisor_handles_missing_company(fake_kpis: list[dict]) -> None:
    from agent import supervisor

    with patch("agent.supervisor.tools.fetch_kpis_for_companies", return_value=fake_kpis), \
         patch("agent.supervisor.retrieval.hybrid_retrieve", return_value=([], 0)):
        out = supervisor.handle(question="Compare Apple, Microsoft, and ZZZCorp", top_k=5, conversation_id=None)

    assert "ZZZCorp" in out["missing_companies"] or "ZZZCorp" in out["answer"] or any("ZZZ" in m for m in out["missing_companies"])


def test_supervisor_with_no_data_falls_back_to_no_source() -> None:
    from agent import supervisor

    with patch("agent.supervisor.tools.fetch_kpis_for_companies", return_value=[]):
        out = supervisor.handle(question="Compare Apple and Microsoft", top_k=5, conversation_id=None)

    assert out["grounded"] is False
    assert out["citations"] == []


def test_section_terms_are_not_extracted_as_companies() -> None:
    from agent import supervisor

    assert supervisor._extract_companies("Compare Risk and MD&A coverage between filings") == []
