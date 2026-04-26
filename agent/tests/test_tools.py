from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock


os.environ.setdefault("DOCINTEL_CATALOG", "test_catalog")
os.environ.setdefault("DOCINTEL_SCHEMA", "test_schema")
os.environ.setdefault("DOCINTEL_WAREHOUSE_ID", "test_warehouse")


def _statement(rows: list[list[object]]) -> SimpleNamespace:
    return SimpleNamespace(
        result=SimpleNamespace(data_array=rows),
        manifest=SimpleNamespace(
            schema=SimpleNamespace(
                columns=[
                    SimpleNamespace(name="filename"),
                    SimpleNamespace(name="company_name"),
                    SimpleNamespace(name="fiscal_year"),
                ]
            )
        ),
    )


def test_fetch_kpis_parameterizes_filename(monkeypatch) -> None:
    from agent import tools

    client = MagicMock()
    client.statement_execution.execute_statement.return_value = _statement(
        [["ACME_10K_2024.pdf", "ACME", 2024]]
    )
    monkeypatch.setattr(tools, "_workspace", lambda: client)

    row = tools.fetch_kpis("ACME_10K_2024.pdf")

    assert row == {
        "filename": "ACME_10K_2024.pdf",
        "company_name": "ACME",
        "fiscal_year": 2024,
    }
    call = client.statement_execution.execute_statement.call_args.kwargs
    assert call["warehouse_id"] == "test_warehouse"
    assert call["parameters"] == [{"name": "filename", "value": "ACME_10K_2024.pdf"}]


def test_fetch_kpis_for_companies_builds_bound_parameters(monkeypatch) -> None:
    from agent import tools

    client = MagicMock()
    client.statement_execution.execute_statement.return_value = _statement(
        [["ACME_10K_2024.pdf", "ACME", 2024]]
    )
    monkeypatch.setattr(tools, "_workspace", lambda: client)

    rows = tools.fetch_kpis_for_companies(["ACME", "BETA"])

    assert rows[0]["company_name"] == "ACME"
    call = client.statement_execution.execute_statement.call_args.kwargs
    assert call["parameters"] == [
        {"name": "c0", "value": "%acme%"},
        {"name": "c1", "value": "%beta%"},
    ]
    assert "gold_filing_kpis" in call["statement"]
