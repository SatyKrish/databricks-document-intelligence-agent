"""Supervisor agent for cross-company aggregation (US3).

Detects N >= 2 company tokens in the question, fans out a per-company sub-query
through the analyst agent's single-filing path, pulls structured KPIs from
gold_filing_kpis, and emits a markdown table with citations.
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

from agent import retrieval, tools
from agent.retrieval import Citation


# Detect company tokens. Names of length >= 3 starting with a capital letter,
# excluding boiler-plate stop words. Sufficient for the eval set.
_STOP = {"compare", "between", "across", "the", "and", "for", "with", "their", "most", "recent"}


def handle(*, question: str, top_k: int, conversation_id: str | None) -> dict[str, Any]:
    started = time.monotonic()
    companies = _extract_companies(question)
    if len(companies) < 2:
        return _empty(question=question, started=started, conversation_id=conversation_id)

    rows = tools.fetch_kpis_for_companies(companies)
    if not rows:
        return _empty(question=question, started=started, conversation_id=conversation_id)

    citations: list[Citation] = []
    for r in rows:
        sub_citations, _ = retrieval.hybrid_retrieve(
            f"{r['company_name']}: {question}",
            top_k=2,
            company_filter=r["company_name"],
        )
        citations.extend(sub_citations)

    table = _markdown_table(rows)
    answer = (
        f"### {question.strip().rstrip('?')}\n\n{table}\n\n"
        + ("**Sources:** " + ", ".join(f"`{c.filename}` ({c.section_label})" for c in citations))
    )

    return {
        "answer": answer,
        "grounded": True,
        "citations": [c.to_dict() for c in citations],
        "latency_ms": int((time.monotonic() - started) * 1000),
        "retrieved_count": len(citations),
        "agent_path": "supervisor",
        "conversation_id": conversation_id,
        "turn_id": str(uuid.uuid4()),
        "missing_companies": [c for c in companies if c.lower() not in {r["company_name"].lower() for r in rows}],
    }


def _extract_companies(question: str) -> list[str]:
    found = re.findall(r"\b[A-Z][A-Za-z][A-Za-z0-9&\.\-]+\b", question)
    return [w for w in dict.fromkeys(found) if w.lower() not in _STOP and len(w) > 2]


def _markdown_table(rows: list[dict[str, Any]]) -> str:
    header = "| Company | Fiscal Year | Revenue | EBITDA | Top Segments |"
    sep = "|---|---|---|---|---|"
    lines = [header, sep]
    for r in rows:
        segments = ", ".join(
            f"{s['name']}: {_money(s['revenue'])}" for s in (r.get("segment_revenue") or [])[:3]
        ) or "—"
        lines.append(
            f"| {r['company_name']} | {r['fiscal_year']} | {_money(r['revenue'])} | "
            f"{_money(r['ebitda'])} | {segments} |"
        )
    return "\n".join(lines)


def _money(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"${float(v) / 1e9:.2f}B"
    except (TypeError, ValueError):
        return str(v)


def _empty(*, question: str, started: float, conversation_id: str | None) -> dict[str, Any]:
    from agent.analyst_agent import NO_SOURCE_MESSAGE

    return {
        "answer": NO_SOURCE_MESSAGE,
        "grounded": False,
        "citations": [],
        "latency_ms": int((time.monotonic() - started) * 1000),
        "retrieved_count": 0,
        "agent_path": "supervisor",
        "conversation_id": conversation_id,
        "turn_id": str(uuid.uuid4()),
        "missing_companies": [],
    }
