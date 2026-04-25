"""Regression tests for supervisor question-aware table rendering."""

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
            "segment_revenue_raw": '[{"name":"iPhone","revenue":200000000000},{"name":"Services","revenue":96000000000}]',
            "top_risks_raw": '["macroeconomic conditions","supply chain"]',
        },
        {
            "filename": "MSFT_10K_2024.pdf",
            "company_name": "Microsoft",
            "fiscal_year": 2024,
            "revenue": 245_000_000_000,
            "ebitda": 130_000_000_000,
            "segment_revenue_raw": '[{"name":"Azure","revenue":105000000000}]',
            "top_risks_raw": '["AI risk","competition"]',
        },
    ]


def _run(question: str, fake_kpis: list[dict]) -> str:
    from agent import supervisor

    with patch("agent.supervisor.tools.fetch_kpis_for_companies", return_value=fake_kpis), \
         patch("agent.supervisor.retrieval.hybrid_retrieve", return_value=([], 0)):
        out = supervisor.handle(question=question, top_k=5, conversation_id=None)
    return out["answer"]


def test_risks_question_renders_risks_column(fake_kpis: list[dict]) -> None:
    answer = _run("Compare top 3 risk factors between Apple and Microsoft", fake_kpis)
    assert "Top Risks" in answer
    assert "Revenue" not in answer.split("|")[1]  # header line shouldn't lead with revenue


def test_segments_question_renders_segments_column(fake_kpis: list[dict]) -> None:
    answer = _run("Compare segment revenue between Apple and Microsoft", fake_kpis)
    assert "Top Segments" in answer
    assert "iPhone" in answer
    assert "Azure" in answer


def test_ebitda_question_renders_ebitda_only(fake_kpis: list[dict]) -> None:
    answer = _run("Compare EBITDA across Apple and Microsoft", fake_kpis)
    # Header should include EBITDA and not Revenue
    header_line = next(line for line in answer.splitlines() if "| Company" in line)
    assert "EBITDA" in header_line
    assert "Revenue" not in header_line


def test_narrative_intent_skips_table(fake_kpis: list[dict]) -> None:
    """Questions about R&D / repurchases / antitrust aren't in gold_filing_kpis
    columns, so the supervisor should NOT fabricate a numeric table for them.
    """
    answer = _run("Compare R&D spending trends between Apple and Microsoft", fake_kpis)
    assert "**Apple**" in answer or "no grounded source" in answer.lower()
    # Critical: must not fabricate a Revenue column for an R&D question.
    assert "| Revenue |" not in answer
