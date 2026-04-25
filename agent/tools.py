"""UC Function tools the Analyst Agent can call for deterministic SQL aggregation.

Wraps gold_filing_kpis so cross-company comparisons (US3) don't have to go through
retrieval + LLM math.
"""

from __future__ import annotations

import os
from typing import Any

from databricks.sdk import WorkspaceClient

from agent._obo import user_workspace


CATALOG = os.environ["DOCINTEL_CATALOG"]
SCHEMA = os.environ["DOCINTEL_SCHEMA"]
WAREHOUSE_ID = os.environ["DOCINTEL_WAREHOUSE_ID"]


def fetch_kpis(filename: str) -> dict[str, Any] | None:
    """Return the gold_filing_kpis row for one filing, or None if not present."""
    w = user_workspace()
    statement = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=(
            f"SELECT * FROM {CATALOG}.{SCHEMA}.gold_filing_kpis "
            "WHERE filename = :filename LIMIT 1"
        ),
        parameters=[{"name": "filename", "value": filename}],
        wait_timeout="30s",
    )
    if not statement.result or not statement.result.data_array:
        return None
    columns = [c.name for c in statement.manifest.schema.columns]
    return dict(zip(columns, statement.result.data_array[0], strict=True))


def fetch_kpis_for_companies(companies: list[str]) -> list[dict[str, Any]]:
    """Best-match per-company KPI lookup. Matches against company_name AND filename to
    handle both human-named filings ("Apple") and ticker-prefixed filenames
    ("AAPL_10K_2024.pdf"). Used by the supervisor for cross-company comparison.
    """
    if not companies:
        return []
    w = user_workspace()
    clauses = []
    parameters: list[dict[str, str]] = []
    for i, c in enumerate(companies):
        needle = f"%{c.lower()}%"
        clauses.append(f"LOWER(company_name) LIKE :c{i} OR LOWER(filename) LIKE :c{i}")
        parameters.append({"name": f"c{i}", "value": needle})
    where = " OR ".join(f"({clause})" for clause in clauses)
    statement = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=(
            f"SELECT * FROM {CATALOG}.{SCHEMA}.gold_filing_kpis "
            f"WHERE {where} "
            "QUALIFY ROW_NUMBER() OVER (PARTITION BY company_name ORDER BY fiscal_year DESC) = 1"
        ),
        parameters=parameters,
        wait_timeout="30s",
    )
    if not statement.result or not statement.result.data_array:
        return []
    columns = [c.name for c in statement.manifest.schema.columns]
    return [dict(zip(columns, row, strict=True)) for row in statement.result.data_array]
