"""UC Function tools the Analyst Agent can call for deterministic SQL aggregation.

Wraps gold_filing_kpis so cross-company comparisons (US3) don't have to go through
retrieval + LLM math.
"""

from __future__ import annotations

import os
from typing import Any

from databricks.sdk import WorkspaceClient


CATALOG = os.environ["DOCINTEL_CATALOG"]
SCHEMA = os.environ["DOCINTEL_SCHEMA"]
WAREHOUSE_ID = os.environ["DOCINTEL_WAREHOUSE_ID"]


def fetch_kpis(filename: str) -> dict[str, Any] | None:
    """Return the gold_filing_kpis row for one filing, or None if not present."""
    w = WorkspaceClient()
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
    """Best-match filename lookup per company. Used by the supervisor for cross-company comparison."""
    if not companies:
        return []
    w = WorkspaceClient()
    placeholders = ", ".join(f":c{i}" for i in range(len(companies)))
    parameters = [
        {"name": f"c{i}", "value": f"%{c.lower()}%"} for i, c in enumerate(companies)
    ]
    statement = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=(
            f"SELECT * FROM {CATALOG}.{SCHEMA}.gold_filing_kpis "
            f"WHERE LOWER(filename) LIKE ANY ({placeholders}) "
            "QUALIFY ROW_NUMBER() OVER (PARTITION BY company_name ORDER BY fiscal_year DESC) = 1"
        ),
        parameters=parameters,
        wait_timeout="30s",
    )
    if not statement.result or not statement.result.data_array:
        return []
    columns = [c.name for c in statement.manifest.schema.columns]
    return [dict(zip(columns, row, strict=True)) for row in statement.result.data_array]
