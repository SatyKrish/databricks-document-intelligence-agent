"""Supervisor agent for cross-company aggregation (US3).

Detects N >= 2 company tokens in the question, classifies the requested metric,
fans out per-company retrieval, and emits a markdown table whose columns match
what was asked. For metrics not in gold_filing_kpis (e.g. risks, R&D trends),
falls back to a per-company narrative built from retrieved sections rather
than fabricating a numeric table.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any

from agent import retrieval, tools
from agent.retrieval import Citation


# Words to exclude when scanning for company tokens. Capitalized but not company names.
_STOP = {
    "compare", "between", "across", "the", "and", "for", "with", "their", "most",
    "recent", "what", "which", "how", "did", "does", "do", "does", "is", "are",
    "vs", "versus", "against", "ebitda", "revenue", "fiscal", "year", "filing",
    "filings", "10-k", "10k", "ten-k", "tenk", "company", "companies", "by",
    "in", "on", "of", "to", "from",
    "mda", "md&a", "risk", "risks", "financials", "notes", "note", "section",
    "sections", "item", "items", "management", "discussion", "analysis",
}

# Map question keywords -> column template + KPI extractor.
_INTENTS: list[tuple[set[str], str]] = [
    ({"segment", "segments"}, "segments"),
    ({"risk", "risks"}, "risks"),
    ({"ebitda"}, "ebitda"),
    ({"r&d", "research", "development", "spending"}, "narrative"),
    ({"acquisitions", "antitrust", "repurchase", "buyback", "climate"}, "narrative"),
    ({"revenue", "sales"}, "revenue"),
]


def handle(*, question: str, top_k: int, conversation_id: str | None) -> dict[str, Any]:
    started = time.monotonic()
    companies = _extract_companies(question)
    if len(companies) < 2:
        return _empty(question=question, started=started, conversation_id=conversation_id)

    rows = tools.fetch_kpis_for_companies(companies)
    if not rows:
        return _empty(question=question, started=started, conversation_id=conversation_id)

    intent = _intent(question)

    # For any structured-table intent, also pull a few citations per company so the
    # answer is verifiable. Narrative intents lean on retrieval entirely.
    per_company_citations: dict[str, list[Citation]] = {}
    for r in rows:
        sub_citations, _ = retrieval.hybrid_retrieve(
            f"{r['company_name']}: {question}",
            top_k=2,
            company_filter=r["company_name"],
        )
        per_company_citations[r["company_name"]] = sub_citations

    if intent == "narrative":
        body = _narrative(rows, per_company_citations, question)
    else:
        body = _table(rows, intent)

    citations = [c for cits in per_company_citations.values() for c in cits]
    sources_line = (
        "**Sources:** " + ", ".join(f"`{c.filename}` ({c.section_label})" for c in citations)
        if citations
        else ""
    )
    answer = f"### {question.strip().rstrip('?')}\n\n{body}\n\n{sources_line}".rstrip()

    found = {r["company_name"].lower() for r in rows}
    missing = [c for c in companies if c.lower() not in found and not any(c.lower() in name for name in found)]

    return {
        "answer": answer,
        "grounded": True,
        "citations": [c.to_dict() for c in citations],
        "latency_ms": int((time.monotonic() - started) * 1000),
        "retrieved_count": len(citations),
        "agent_path": "supervisor",
        "conversation_id": conversation_id,
        "turn_id": str(uuid.uuid4()),
        "missing_companies": missing,
    }


def _extract_companies(question: str) -> list[str]:
    found = re.findall(r"\b[A-Z][A-Za-z][A-Za-z0-9&\.\-]+\b", question)
    return [w for w in dict.fromkeys(found) if w.lower() not in _STOP and len(w) > 2]


def _intent(question: str) -> str:
    lower = question.lower()
    for keywords, intent in _INTENTS:
        if any(k in lower for k in keywords):
            return intent
    return "general"


def _table(rows: list[dict[str, Any]], intent: str) -> str:
    if intent == "segments":
        header = "| Company | Fiscal Year | Top Segments |"
        sep = "|---|---|---|"
        body = []
        for r in rows:
            segments = _segments_text(r.get("segment_revenue_raw") or r.get("segment_revenue"))
            body.append(f"| {r['company_name']} | {r.get('fiscal_year', '—')} | {segments} |")
        return "\n".join([header, sep, *body])

    if intent == "risks":
        header = "| Company | Fiscal Year | Top Risks |"
        sep = "|---|---|---|"
        body = []
        for r in rows:
            risks = _risks_text(r.get("top_risks_raw") or r.get("top_risks"))
            body.append(f"| {r['company_name']} | {r.get('fiscal_year', '—')} | {risks} |")
        return "\n".join([header, sep, *body])

    if intent == "ebitda":
        header = "| Company | Fiscal Year | EBITDA |"
        sep = "|---|---|---|"
        body = [f"| {r['company_name']} | {r.get('fiscal_year', '—')} | {_money(r.get('ebitda'))} |" for r in rows]
        return "\n".join([header, sep, *body])

    if intent == "revenue":
        header = "| Company | Fiscal Year | Revenue |"
        sep = "|---|---|---|"
        body = [f"| {r['company_name']} | {r.get('fiscal_year', '—')} | {_money(r.get('revenue'))} |" for r in rows]
        return "\n".join([header, sep, *body])

    # general: include all KPI columns
    header = "| Company | Fiscal Year | Revenue | EBITDA | Top Segments |"
    sep = "|---|---|---|---|---|"
    body = []
    for r in rows:
        segments = _segments_text(r.get("segment_revenue_raw") or r.get("segment_revenue"))
        body.append(
            f"| {r['company_name']} | {r.get('fiscal_year', '—')} | "
            f"{_money(r.get('revenue'))} | {_money(r.get('ebitda'))} | {segments} |"
        )
    return "\n".join([header, sep, *body])


def _narrative(
    rows: list[dict[str, Any]],
    citations_by_company: dict[str, list[Citation]],
    question: str,
) -> str:
    """For metrics not in gold_filing_kpis, summarize per-company from retrieved snippets."""
    parts = []
    for r in rows:
        company = r["company_name"]
        cits = citations_by_company.get(company, [])
        if not cits:
            parts.append(f"**{company}** — no grounded source for this question in the indexed corpus.")
            continue
        snippet = next((c.snippet for c in cits if c.snippet), None) or "no detail available in retrieved snippets."
        parts.append(f"**{company}** — {snippet}")
    return "\n\n".join(parts)


def _money(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"${float(v) / 1e9:.2f}B"
    except (TypeError, ValueError):
        return str(v)


def _segments_text(raw: Any) -> str:
    items = _coerce_list(raw)
    if not items:
        return "—"
    rendered = []
    for item in items[:3]:
        if isinstance(item, dict):
            rendered.append(f"{item.get('name','?')}: {_money(item.get('revenue'))}")
        else:
            rendered.append(str(item))
    return ", ".join(rendered)


def _risks_text(raw: Any) -> str:
    items = _coerce_list(raw)
    if not items:
        return "—"
    return "; ".join(str(x) for x in items[:5])


def _coerce_list(raw: Any) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        try:
            v = json.loads(s)
            return v if isinstance(v, list) else [v]
        except (ValueError, TypeError):
            return [s]
    return [raw]


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
