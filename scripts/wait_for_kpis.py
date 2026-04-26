"""Poll until gold_filing_kpis has at least N rows, or time out.

Used by both `scripts/bootstrap-demo.sh` (post-pipeline-trigger) and the GitHub
Actions deploy workflow (post-sample-upload). Centralized here so both paths
share the same SQL Statement Execution logic.

Required env:
    DOCINTEL_CATALOG       e.g. workspace
    DOCINTEL_SCHEMA        e.g. docintel_10k_demo
    DOCINTEL_WAREHOUSE_ID  SQL warehouse to run the count query

CLI:
    --min-rows N    minimum rows expected (default 1)
    --timeout SECS  poll budget (default 600)
    --poll-secs N   sleep between polls (default 15)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from databricks.sdk import WorkspaceClient


def _count(w: WorkspaceClient, *, warehouse_id: str, table: str) -> int:
    out = w.api_client.do(
        "POST",
        "/api/2.0/sql/statements",
        body={
            "warehouse_id": warehouse_id,
            "statement": f"SELECT count(*) AS n FROM {table}",
            "wait_timeout": "30s",
            "on_wait_timeout": "CANCEL",
        },
    )
    rows = (out.get("result") or {}).get("data_array") or []
    if not rows:
        return 0
    try:
        return int(rows[0][0])
    except (TypeError, ValueError):
        return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--min-rows", type=int, default=1)
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--poll-secs", type=int, default=15)
    args = p.parse_args()

    catalog = os.environ["DOCINTEL_CATALOG"]
    schema = os.environ["DOCINTEL_SCHEMA"]
    warehouse_id = os.environ["DOCINTEL_WAREHOUSE_ID"]
    table = f"{catalog}.{schema}.gold_filing_kpis"

    w = WorkspaceClient()
    deadline = time.monotonic() + args.timeout
    while True:
        try:
            n = _count(w, warehouse_id=warehouse_id, table=table)
        except Exception as exc:
            print(f"[wait_for_kpis] count error (retrying): {exc}", file=sys.stderr)
            n = 0
        if n >= args.min_rows:
            print(f"[wait_for_kpis] {table} has {n} row(s) (>= {args.min_rows})")
            return 0
        if time.monotonic() >= deadline:
            print(
                f"[wait_for_kpis] timed out after {args.timeout}s; {table} has {n} row(s) "
                f"(needed {args.min_rows})",
                file=sys.stderr,
            )
            return 1
        print(f"[wait_for_kpis] {table} has {n} row(s); waiting…", file=sys.stderr)
        time.sleep(args.poll_secs)


if __name__ == "__main__":
    sys.exit(main())
