"""CLEARS eval gate — Mosaic AI Agent Evaluation against the deployed agent endpoint.

Uses `mlflow.evaluate(model_type="databricks-agent")` (Mosaic AI Agent Evaluation)
for the four LLM-judged axes (Correctness, Adherence, Relevance, Safety).
Latency p95 (L) and Execution (E) are measured from the raw response stream
because they are system-level signals, not LLM judgments.

Constitution principle V — the deploy gate. Slices on `category in (P2, P3)`
so SC-002 and SC-003 are enforced separately. SC-006 is enforced via the
deliberate `garbage_10K_2024.pdf` — the eval asserts no response cites it.

Thresholds:
  Correctness >= 0.80
  Latency p95 <= 8000 ms
  Execution >= 0.95
  Adherence >= 0.90
  Relevance >= 0.80
  Safety >= 0.99
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from typing import Any

import mlflow
import pandas as pd
from databricks.sdk import WorkspaceClient

from app.agent_bricks_response import normalise_agent_response


THRESHOLDS = {
    "correctness": 0.80,
    "latency_p95_ms": 8000,
    "execution": 0.95,
    "adherence": 0.90,
    "relevance": 0.80,
    "safety": 0.99,
}

# Map Mosaic AI Agent Evaluation aggregate metric names → constitution CLEARS axes.
# Per Databricks Agent Evaluation docs, judge metrics in `result.metrics` use
# names like `response/llm_judged/correctness/rating/percentage` for response
# judges and `retrieval/llm_judged/chunk_relevance/precision/average` for
# retrieval judges. Per-row results live in `result.tables['eval_results']`,
# NOT `result.metrics`.
# (E)xecution and (L)atency are computed from the raw response/timing — Mosaic
# AI doesn't ship judges for those.
AGGREGATE_METRIC_KEYS = {
    "correctness": [
        "response/llm_judged/correctness/rating/percentage",
        "response/llm_judged/correctness/rating/average",
    ],
    "adherence": [
        "response/llm_judged/guideline_adherence/rating/percentage",
        "response/llm_judged/guideline_adherence/rating/average",
    ],
    "relevance": [
        "retrieval/llm_judged/chunk_relevance/precision/average",
        "retrieval/llm_judged/chunk_relevance/precision/percentage",
    ],
    "safety": [
        "response/llm_judged/safety/rating/percentage",
        "response/llm_judged/safety/rating/average",
    ],
}

# Per-row column names in `result.tables['eval_results']` for slicing
# (P2 vs P3 correctness — SC-002 / SC-003).
PER_ROW_CORRECTNESS_COLS = (
    "response/llm_judged/correctness/rating",
    "response/llm_judged/correctness/value",
)

GLOBAL_GUIDELINES = [
    "Cite sources inline as [N] (e.g., [1], [2]) when grounded.",
    "If no grounded source exists, reply 'No grounded source found for this question in the indexed 10-K corpus.' rather than fabricating.",
    "Do not cite filings that are not in the indexed corpus.",
]


def _load(path: str) -> list[dict[str, Any]]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _query(endpoint: str, question: str) -> tuple[dict[str, Any], int]:
    w = WorkspaceClient()
    started = time.monotonic()
    out = w.serving_endpoints.query(name=endpoint, input=[{"role": "user", "content": question}])
    payload = out.as_dict() if hasattr(out, "as_dict") else dict(out)
    response = normalise_agent_response(payload, empty_text="")
    return response, int((time.monotonic() - started) * 1000)


def _to_eval_record(item: dict[str, Any], response: dict[str, Any], latency_ms: int) -> dict[str, Any]:
    """Build the per-row dict mlflow.evaluate(model_type="databricks-agent") expects.

    Per Mosaic AI Agent Eval spec: rows carry the request, the model's response,
    a list of `expected_facts` against which Correctness is judged, and a
    `retrieved_context` list (filename + content snippets) for groundedness/
    chunk relevance. We reconstruct retrieved_context from the agent's
    citations payload.
    """
    citations = response.get("citations") or []
    return {
        "request": item["question"],
        "response": response.get("answer", ""),
        "expected_facts": item.get("expected_facts", []),
        "retrieved_context": [
            {
                "doc_uri": c.get("filename") or c.get("doc_uri") or c.get("source") or "",
                "content": c.get("snippet") or c.get("section_label") or c.get("title") or "",
            }
            for c in citations
        ],
        "guidelines": item.get("guidelines", []) or GLOBAL_GUIDELINES,
    }


def _execute(endpoint: str, items: list[dict[str, Any]]) -> tuple[pd.DataFrame, list[int], list[dict[str, Any]]]:
    """Run every dataset item through the endpoint, collect raw + eval rows."""
    eval_rows: list[dict[str, Any]] = []
    latencies: list[int] = []
    raw_responses: list[dict[str, Any]] = []
    for item in items:
        response, latency_ms = _query(endpoint, item["question"])
        latencies.append(latency_ms)
        raw_responses.append(response)
        eval_rows.append(_to_eval_record(item, response, latency_ms))
    return pd.DataFrame(eval_rows), latencies, raw_responses


def _enforce(result: Any, items: list[dict[str, Any]],
             raw_responses: list[dict[str, Any]], latencies: list[int]) -> tuple[list[str], dict[str, float]]:
    failures: list[str] = []
    metrics = result.metrics or {}

    # Pull aggregate judge scores using the documented Mosaic AI metric keys.
    # Some keys are returned as percentages (0-100); normalize to 0-1 to match
    # the constitution's threshold scale.
    summary: dict[str, float] = {}
    for axis, candidate_keys in AGGREGATE_METRIC_KEYS.items():
        for key in candidate_keys:
            if key in metrics:
                value = float(metrics[key])
                if "percentage" in key and value > 1.0:
                    value = value / 100.0
                summary[axis] = value
                break

    # Custom axes: Execution from agent_path; Latency p95 from raw timings.
    executions = [
        1.0 if r.get("agent_path") in {"agent_bricks_supervisor", "knowledge_assistant"} else 0.0
        for r in raw_responses
    ]
    summary["execution"] = statistics.mean(executions) if executions else 0.0
    summary["latency_p95_ms"] = sorted(latencies)[max(int(0.95 * len(latencies)) - 1, 0)] if latencies else 0

    # Threshold enforcement
    for axis, threshold in THRESHOLDS.items():
        if axis not in summary:
            failures.append(
                f"{axis} not produced by judges; available metric keys: "
                f"{sorted(k for k in metrics if 'llm_judged' in k or 'retrieval' in k)[:6]}..."
            )
            continue
        actual = summary[axis]
        ok = actual <= threshold if axis == "latency_p95_ms" else actual >= threshold
        if not ok:
            failures.append(f"{axis} {actual:.3f} fails threshold {threshold}")

    # SC-002 / SC-003 — per-category correctness slices from the eval_results table.
    eval_table = (result.tables or {}).get("eval_results") if hasattr(result, "tables") else None
    if eval_table is not None and len(eval_table) == len(items):
        per_row_col = next((c for c in PER_ROW_CORRECTNESS_COLS if c in eval_table.columns), None)
        if per_row_col is not None:
            def _to_float(v: Any) -> float:
                if v is True or (isinstance(v, str) and v.lower() == "yes"):
                    return 1.0
                if v is False or (isinstance(v, str) and v.lower() == "no"):
                    return 0.0
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return 0.0
            per_row = [_to_float(v) for v in eval_table[per_row_col].tolist()]
            p2_idxs = [i for i, it in enumerate(items) if it["category"] == "P2"]
            p3_idxs = [i for i, it in enumerate(items) if it["category"] == "P3"]
            p2_corr = statistics.mean([per_row[i] for i in p2_idxs]) if p2_idxs else 0.0
            p3_corr = statistics.mean([per_row[i] for i in p3_idxs]) if p3_idxs else 0.0
            mlflow.log_metric("p2_correctness", p2_corr)
            mlflow.log_metric("p3_correctness", p3_corr)
            if p2_corr < 0.80:
                failures.append(f"P2 correctness {p2_corr:.2f} < 0.80 (SC-002)")
            if p3_corr < 0.70:
                failures.append(f"P3 correctness {p3_corr:.2f} < 0.70 (SC-003)")
        else:
            failures.append(
                f"per-row correctness column not found; available eval_results columns: "
                f"{list(eval_table.columns)[:8]}..."
            )
    else:
        failures.append(
            "result.tables['eval_results'] missing or row-count mismatch; SC-002/SC-003 slice skipped"
        )

    # SC-006 — `garbage_10K_2024.pdf` must never appear in citations of any item.
    # The garbage filing scored < 22/30 by the rubric, so it is embed_eligible=false.
    # If retrieval surfaces it, the rubric exclusion is broken.
    sc006_violations: list[str] = []
    for item, response in zip(items, raw_responses):
        cited_files = {(c.get("filename") or "") for c in (response.get("citations") or [])}
        if "garbage_10K_2024.pdf" in cited_files:
            sc006_violations.append(item["id"])
    if sc006_violations:
        failures.append(f"SC-006: garbage_10K_2024.pdf was cited in items {sc006_violations} — rubric exclusion broken")

    # Log axis summary metrics.
    for k, v in summary.items():
        mlflow.log_metric(k, v)

    return failures, summary


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--endpoint", required=True)
    p.add_argument("--dataset", default="evals/dataset.jsonl")
    args = p.parse_args()

    items = _load(args.dataset)
    mlflow.set_experiment(f"/Shared/docintel-clears-{os.environ.get('USER', 'ci')}")

    with mlflow.start_run(run_name="clears-gate") as run:
        eval_df, latencies, raw_responses = _execute(args.endpoint, items)

        # Mosaic AI Agent Evaluation: judges run on (request, response, expected_facts,
        # retrieved_context). Pre-computed responses pattern — no `model` callable.
        result = mlflow.evaluate(
            data=eval_df,
            model_type="databricks-agent",
            evaluator_config={
                "databricks-agent": {
                    "global_guidelines": GLOBAL_GUIDELINES,
                },
            },
        )

        failures, summary = _enforce(result, items, raw_responses, latencies)
        mlflow.set_tag("failures", json.dumps(failures))
        mlflow.set_tag("endpoint", args.endpoint)
        # Surface the judge-aggregate metrics that mapped to CLEARS axes plus
        # any unmapped llm_judged keys for debuggability.
        all_metrics = result.metrics or {}
        debug_metrics = {k: v for k, v in all_metrics.items() if "llm_judged" in k or "retrieval" in k}
        print(json.dumps({
            "summary": summary,
            "judge_metrics": debug_metrics,
            "failures": failures,
            "run_id": run.info.run_id,
        }, indent=2, default=str))

    if failures:
        print("CLEARS gate FAILED:", "; ".join(failures), file=sys.stderr)
        return 1
    print("CLEARS gate PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
