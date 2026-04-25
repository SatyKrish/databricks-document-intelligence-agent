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

log "step 2a/5: copying synthetic samples to $VOLUME_PATH"
shopt -s nullglob
sample_pdfs=(samples/*_10K_*.pdf)
shopt -u nullglob
if (( ${#sample_pdfs[@]} == 0 )); then
  log "no PDFs in samples/; run samples/synthesize.py to regenerate"
else
  for pdf in "${sample_pdfs[@]}"; do
    databricks fs cp "$pdf" "$VOLUME_PATH/" --overwrite || \
      log "fs cp $pdf failed — continuing"
  done
fi

log "step 2b/5: triggering pipeline run ($PIPELINE_KEY)"
databricks bundle run -t "$TARGET" "$PIPELINE_KEY" || \
  die "pipeline run failed — inspect SDP UI before retrying"

log "step 3/5: waiting up to ${WAIT_SECONDS}s for $KPI_TABLE to have >= 1 row"
"$PYTHON" scripts/wait_for_kpis.py --min-rows 1 --timeout "$WAIT_SECONDS" || \
  die "timed out waiting for $KPI_TABLE; rerun with DOCINTEL_WAIT_SECONDS=<bigger>"

log "step 4/5: registering agent model and repointing $ENDPOINT"
"$PYTHON" agent/log_and_register.py --target "$TARGET" --serving-endpoint "$ENDPOINT" || \
  die "agent registration failed"

log "step 5/5: final deploy — serving/monitor/app should now resolve cleanly"
databricks bundle deploy -t "$TARGET"

# Apps deploy: skill databricks-apps/references/platform-guide.md §Deployment
# Workflow — `bundle deploy` uploads code; the app config + restart needs an
# explicit `bundle run`.
log "step 5b/5: applying app config + restart (analyst_app)"
databricks bundle run -t "$TARGET" analyst_app || \
  log "warn: analyst_app run failed — fix the app and retry 'databricks bundle run -t $TARGET analyst_app'"

# OBO scope verification: `bundle run` may wipe user_api_scopes
# (skill platform-guide §"Destructive Updates Warning").
APP_NAME="doc-intel-analyst-${TARGET}"
log "step 5c/5: verifying OBO scopes on $APP_NAME"
databricks apps get "$APP_NAME" --output json > /tmp/docintel-app.json || \
  log "warn: could not fetch app state for scope verification"
if [[ -f /tmp/docintel-app.json ]]; then
  if ! "$PYTHON" -c "
import json, sys
app = json.load(open('/tmp/docintel-app.json'))
scopes = set(app.get('user_api_scopes') or [])
required = {'sql'}
missing = required - scopes
if missing:
    print(f'OBO scopes missing: {sorted(missing)} (got {sorted(scopes)})', file=sys.stderr)
    sys.exit(1)
print(f'OBO scopes intact: {sorted(scopes)}')
"; then
    log "warn: OBO scopes wiped — re-apply via 'databricks apps update $APP_NAME --user-api-scopes sql,iam.access-control:read,iam.current-user:read'"
  fi
fi

# Schema-level UC grants (not declarative in DAB as of CLI 0.298 — skill
# resource-permissions.md only lists volumes for native `grants:`).
ANALYST_GROUP="${DOCINTEL_ANALYST_GROUP:-account users}"
log "step 5d/5: applying schema grants for ${ANALYST_GROUP} on ${DOCINTEL_CATALOG}.${DOCINTEL_SCHEMA}"
databricks api post \
  "/api/2.1/unity-catalog/permissions/SCHEMA/${DOCINTEL_CATALOG}.${DOCINTEL_SCHEMA}" \
  --json "{\"changes\":[{\"principal\":\"${ANALYST_GROUP}\",\"add\":[\"USE_SCHEMA\",\"SELECT\",\"EXECUTE\"]}]}" \
  >/dev/null 2>&1 || \
  log "warn: schema grants call failed (may already be applied; UC dedupes)"

log "done. Endpoint: $ENDPOINT  |  KPI table: $KPI_TABLE  |  App: $APP_NAME"
log "next: $PYTHON evals/clears_eval.py --endpoint $ENDPOINT --dataset evals/dataset.jsonl"
