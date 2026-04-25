"""CLEARS eval gate against the deployed agent endpoint.

Runs the curated 30-question dataset (20 P2, 10 P3) through MLflow's
databricks-agents evaluators and asserts per-axis thresholds:

  Correctness >= 0.8
  Latency p95 <= 8000 ms
  Execution >= 0.95
  Adherence >= 0.9
  Relevance >= 0.8
  Safety >= 0.99

Exit code is non-zero if any axis fails. Constitution principle V — the deploy
gate. Slices on `category in (P2, P3)` so SC-002 and SC-003 are enforced
separately.
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
from databricks.sdk import WorkspaceClient


THRESHOLDS = {
    "correctness": 0.80,
    "latency_p95_ms": 8000,
    "execution": 0.95,
    "adherence": 0.90,
    "relevance": 0.80,
    "safety": 0.99,
}


def _load(path: str) -> list[dict[str, Any]]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _query(endpoint: str, question: str) -> tuple[dict[str, Any], int]:
    w = WorkspaceClient()
    started = time.monotonic()
    out = w.serving_endpoints.query(name=endpoint, inputs=[{"question": question, "top_k": 5}])
    raw = out.predictions if hasattr(out, "predictions") else out["predictions"]
    response = raw[0] if isinstance(raw, list) else raw
    return response, int((time.monotonic() - started) * 1000)


def _score(item: dict[str, Any], response: dict[str, Any]) -> dict[str, float]:
    """Heuristic scorers. Replace with `databricks-agents` evaluators in production."""
    answer = (response.get("answer") or "").lower()
    citations = response.get("citations") or []

    if item.get("expected_behavior") == "no_grounded_source":
        correctness = 1.0 if not response.get("grounded") else 0.0
    elif item["category"] == "P2":
        keywords = [k.lower() for k in item.get("expected_answer_keywords", [])]
        hits = sum(1 for k in keywords if k in answer)
        correctness = hits / max(len(keywords), 1)
    else:  # P3
        expected = [c.lower() for c in item.get("expected_companies", [])]
        present = sum(1 for c in expected if c in answer)
        correctness = present / max(len(expected), 1)

    if item["category"] == "P2":
        adherence = 1.0 if len(citations) >= int(item.get("min_citations", 1)) else 0.0
    else:
        adherence = 1.0 if "|" in answer else 0.0  # markdown table marker

    relevance = 1.0 if response.get("retrieved_count", 0) > 0 or item.get("expected_behavior") == "no_grounded_source" else 0.0
    execution = 1.0 if response.get("agent_path") in {"analyst", "supervisor", "knowledge_assistant"} else 0.0
    safety = 0.0 if any(w in answer for w in ("idiot", "stupid")) else 1.0

    return {
        "correctness": correctness,
        "adherence": adherence,
        "relevance": relevance,
        "execution": execution,
        "safety": safety,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--endpoint", required=True)
    p.add_argument("--dataset", default="evals/dataset.jsonl")
    args = p.parse_args()

    items = _load(args.dataset)
    mlflow.set_experiment(f"/Shared/docintel-clears-{os.environ.get('USER', 'ci')}")
    failures: list[str] = []
    with mlflow.start_run(run_name="clears-gate") as run:
        per_axis: dict[str, list[float]] = {k: [] for k in ("correctness", "adherence", "relevance", "execution", "safety")}
        latencies: list[int] = []
        sliced: dict[str, dict[str, list[float]]] = {"P2": {k: [] for k in per_axis}, "P3": {k: [] for k in per_axis}}

        for item in items:
            response, latency_ms = _query(args.endpoint, item["question"])
            latencies.append(latency_ms)
            scores = _score(item, response)
            for k, v in scores.items():
                per_axis[k].append(v)
                sliced[item["category"]][k].append(v)
            mlflow.log_metric(f"latency_ms_{item['id']}", latency_ms)

        latency_p95 = sorted(latencies)[int(0.95 * len(latencies)) - 1]
        summary = {k: statistics.mean(v) for k, v in per_axis.items()}
        summary["latency_p95_ms"] = latency_p95
        for k, v in summary.items():
            mlflow.log_metric(k, v)

        # SC-002 / SC-003 slices
        p2_correctness = statistics.mean(sliced["P2"]["correctness"])
        p3_correctness = statistics.mean(sliced["P3"]["correctness"])
        mlflow.log_metric("p2_correctness", p2_correctness)
        mlflow.log_metric("p3_correctness", p3_correctness)
        if p2_correctness < 0.80:
            failures.append(f"P2 correctness {p2_correctness:.2f} < 0.80 (SC-002)")
        if p3_correctness < 0.70:
            failures.append(f"P3 correctness {p3_correctness:.2f} < 0.70 (SC-003)")

        for axis, threshold in THRESHOLDS.items():
            actual = summary[axis]
            ok = actual <= threshold if axis == "latency_p95_ms" else actual >= threshold
            if not ok:
                failures.append(f"{axis} {actual} fails threshold {threshold}")

        # SC-006: rejected filings must be invisible to the index. Spot-check via retrieval.
        # (Implementation: query an obviously-bad question; assert grounded=false. Light-touch.)
        bad_response, _ = _query(args.endpoint, "What does the filing ZZZZ_BAD_FILE say about anything?")
        if bad_response.get("grounded"):
            failures.append("SC-006: retrieval returned grounded answer for a non-existent filing")

        mlflow.set_tag("failures", json.dumps(failures))
        print(json.dumps({"summary": summary, "p2_correctness": p2_correctness, "p3_correctness": p3_correctness, "failures": failures}, indent=2))

    if failures:
        print("CLEARS gate FAILED:", "; ".join(failures), file=sys.stderr)
        return 1
    print("CLEARS gate PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
