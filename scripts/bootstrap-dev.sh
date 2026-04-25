#!/usr/bin/env bash
# Bootstrap a fresh dev workspace end-to-end.
#
# The bundle has three first-deploy ordering gaps (see docs/runbook.md
# §"Known deploy ordering gaps"): the serving endpoint references a model
# version that doesn't yet exist, the Lakehouse Monitor attaches to a table
# the pipeline must create, and the App + Lakebase catalog race the
# database_instance bring-up. This script orchestrates the workaround:
#
#   1. First deploy (errors expected on serving/monitor/app — tolerated).
#   2. Trigger the pipeline so gold_filing_kpis materialises.
#   3. Wait for the table to have at least one row.
#   4. Register the agent model and repoint the serving endpoint.
#   5. Re-deploy — everything resolves.
#
# Required env vars:
#   DOCINTEL_CATALOG       e.g. workspace
#   DOCINTEL_SCHEMA        e.g. docintel_10k_dev
#   DOCINTEL_WAREHOUSE_ID  SQL warehouse used to poll for the kpi table
#
# Optional:
#   DOCINTEL_TARGET        bundle target (default: dev)
#   DOCINTEL_SAMPLE_PDF    path to a sample PDF to drop in raw_filings
#                          (default: samples/ACME_10K_2024.pdf)
#   DOCINTEL_WAIT_SECONDS  poll timeout for step 3 (default: 600)

set -euo pipefail

log() { echo "[bootstrap] $*" >&2; }
die() { log "error: $*"; exit 1; }

: "${DOCINTEL_CATALOG:?must be set (e.g. workspace)}"
: "${DOCINTEL_SCHEMA:?must be set (e.g. docintel_10k_dev)}"
: "${DOCINTEL_WAREHOUSE_ID:?must be set}"

TARGET="${DOCINTEL_TARGET:-dev}"
SAMPLE_PDF="${DOCINTEL_SAMPLE_PDF:-samples/ACME_10K_2024.pdf}"
WAIT_SECONDS="${DOCINTEL_WAIT_SECONDS:-600}"
ENDPOINT="analyst-agent-${TARGET}"
KPI_TABLE="${DOCINTEL_CATALOG}.${DOCINTEL_SCHEMA}.gold_filing_kpis"
VOLUME_PATH="dbfs:/Volumes/${DOCINTEL_CATALOG}/${DOCINTEL_SCHEMA}/raw_filings"
PIPELINE_KEY="doc_intel_pipeline"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

# Prefer the repo's virtualenv if present (macOS doesn't ship a `python`); fall back to system python3.
if [[ -x ".venv/bin/python" ]]; then
  PYTHON=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
else
  die "no python interpreter found (.venv/bin/python or python3)"
fi

log "step 1/5: initial deploy (serving/monitor/app errors are expected and tolerated)"
if ! databricks bundle deploy -t "$TARGET"; then
  log "first deploy reported errors — continuing per ordering-gap workaround"
fi

if [[ -f "$SAMPLE_PDF" ]]; then
  log "step 2a/5: copying $SAMPLE_PDF to $VOLUME_PATH"
  databricks fs cp "$SAMPLE_PDF" "$VOLUME_PATH/" --overwrite || \
    log "fs cp failed — assuming a sample is already present and continuing"
else
  log "step 2a/5: no sample PDF at $SAMPLE_PDF; assuming raw_filings already populated"
fi

log "step 2b/5: triggering pipeline run ($PIPELINE_KEY)"
databricks bundle run -t "$TARGET" "$PIPELINE_KEY" || \
  die "pipeline run failed — inspect SDP UI before retrying"

log "step 3/5: waiting up to ${WAIT_SECONDS}s for $KPI_TABLE to have >= 1 row"
deadline=$(( $(date +%s) + WAIT_SECONDS ))
while :; do
  count_json=$(databricks api post /api/2.0/sql/statements --json "$(cat <<JSON
{
  "warehouse_id": "${DOCINTEL_WAREHOUSE_ID}",
  "statement": "SELECT count(*) AS n FROM ${KPI_TABLE}",
  "wait_timeout": "30s",
  "on_wait_timeout": "CANCEL"
}
JSON
)" 2>/dev/null || echo '{}')
  n=$(printf '%s' "$count_json" | "$PYTHON" -c 'import json,sys
try:
    d=json.load(sys.stdin)
    rows=d.get("result",{}).get("data_array") or []
    print(int(rows[0][0]) if rows else 0)
except Exception:
    print(0)')
  if [[ "${n:-0}" -gt 0 ]]; then
    log "  → $KPI_TABLE has $n row(s); proceeding"
    break
  fi
  if (( $(date +%s) >= deadline )); then
    die "timed out waiting for $KPI_TABLE; rerun with DOCINTEL_WAIT_SECONDS=<bigger>"
  fi
  sleep 15
done

log "step 4/5: registering agent model and repointing $ENDPOINT"
"$PYTHON" agent/log_and_register.py --target "$TARGET" --serving-endpoint "$ENDPOINT" || \
  die "agent registration failed"

log "step 5/5: final deploy — serving/monitor/app should now resolve cleanly"
databricks bundle deploy -t "$TARGET"

log "done. Endpoint: $ENDPOINT  |  KPI table: $KPI_TABLE"
log "next: python evals/clears_eval.py --endpoint $ENDPOINT --dataset evals/dataset.jsonl"
