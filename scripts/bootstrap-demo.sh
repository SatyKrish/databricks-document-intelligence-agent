#!/usr/bin/env bash
# Bootstrap a demo workspace end-to-end.
#
# Two modes, auto-detected:
#
#   FIRST DEPLOY (no Agent Bricks supervisor endpoint yet)
#     resources/ has chicken-egg dependencies: consumers (app, monitor,
#     lakebase catalog, index-refresh job) need foundation data (populated KPI
#     table, Vector Search index, Agent Bricks endpoint, AVAILABLE Lakebase).
#     DAB deploys everything in one shot, so we stage:
#       1. Hide resources/consumers/*.yml → *.yml.skip; bundle deploy
#          touches only foundation. Trap restores on any exit.
#       2. Produce data: samples → pipeline → wait for KPIs → materialize
#          VS index → configure Agent Bricks → wait for Lakebase AVAILABLE.
#       3. Restore consumer YAMLs; bundle deploy full bundle. All deps
#          satisfied; consumers create cleanly.
#
#   STEADY STATE (consumers already exist)
#     The temp-rename trick is unsafe here: DAB tracks resource state and
#     would plan to DELETE any resource that disappears from config (per
#     Databricks bundle docs — removed config = removed workspace resource).
#     So in steady state we do a normal full bundle deploy and refresh data
#     in place: samples → pipeline → sync the index → update Agent Bricks.
#
# Common to both: bundle run analyst_app (apply config + restart),
# UC grants chain, smoke check.
#
# Required env vars:
#   DOCINTEL_CATALOG       e.g. workspace
#   DOCINTEL_SCHEMA        e.g. docintel_10k_demo
#   DOCINTEL_WAREHOUSE_ID  SQL warehouse id (used by wait_for_kpis + smoke)
#
# Optional:
#   DOCINTEL_TARGET            bundle target (default: demo)
#   DOCINTEL_ANALYST_GROUP     UC group for grants (default: "account users")
#   DOCINTEL_WAIT_SECONDS      poll timeout for KPI table (default: 600)
#   DOCINTEL_LAKEBASE_TIMEOUT  poll timeout for Lakebase (default: 600)
#   DOCINTEL_FORCE_FIRST       set to 1 to force the staged first-deploy path
#   DOCINTEL_FORCE_LOCK        set to 1 to pass --force-lock (use ONLY when a
#                              prior deploy crashed and left a stale lock —
#                              not a normal-flow flag).
#   DOCINTEL_AUTO_APPROVE      set to 1 to pass --auto-approve when intentionally
#                              deleting/recreating stale bundle-managed resources.
#   DOCINTEL_EMBEDDING_ENDPOINT
#                              embedding endpoint for first-run VS index
#                              materialization (default: databricks-bge-large-en)

set -euo pipefail

log() { echo "[bootstrap] $*" >&2; }
die() { log "error: $*"; exit 1; }

: "${DOCINTEL_CATALOG:?must be set (e.g. workspace)}"
: "${DOCINTEL_SCHEMA:?must be set (e.g. docintel_10k_demo)}"
: "${DOCINTEL_WAREHOUSE_ID:?must be set}"

TARGET="${DOCINTEL_TARGET:-demo}"
ANALYST_GROUP="${DOCINTEL_ANALYST_GROUP:-account users}"
WAIT_SECONDS="${DOCINTEL_WAIT_SECONDS:-600}"
LAKEBASE_TIMEOUT="${DOCINTEL_LAKEBASE_TIMEOUT:-600}"
EMBEDDING_ENDPOINT="${DOCINTEL_EMBEDDING_ENDPOINT:-databricks-bge-large-en}"
APP_NAME="doc-intel-analyst-${TARGET}"
KPI_TABLE="${DOCINTEL_CATALOG}.${DOCINTEL_SCHEMA}.gold_filing_kpis"
VOLUME_PATH="dbfs:/Volumes/${DOCINTEL_CATALOG}/${DOCINTEL_SCHEMA}/raw_filings"
PIPELINE_KEY="doc_intel_pipeline"
AGENT_ENDPOINT_NAME=""

DEPLOY_FLAGS=()
if [[ "${DOCINTEL_FORCE_LOCK:-0}" == "1" ]]; then
  log "DOCINTEL_FORCE_LOCK=1 — passing --force-lock to bundle deploy (use only for stale-lock recovery)"
  DEPLOY_FLAGS+=(--force-lock)
fi
if [[ "${DOCINTEL_AUTO_APPROVE:-0}" == "1" ]]; then
  log "DOCINTEL_AUTO_APPROVE=1 — passing --auto-approve to bundle deploy for intentional clean recreation"
  DEPLOY_FLAGS+=(--auto-approve)
fi

# Pin the bundle's `warehouse_id` variable to the user-selected ID so the
# dashboard and Agent Bricks bootstrap match wait_for_kpis.
# Without this, the bundle falls back to its `lookup: warehouse: Serverless
# Starter Warehouse` default — which fails validation in workspaces lacking
# that named warehouse, and silently picks a different ID otherwise.
VAR_FLAGS=(--var "warehouse_id=$DOCINTEL_WAREHOUSE_ID")
BUNDLE_VAR_FLAGS=("${VAR_FLAGS[@]}")

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

if [[ -x ".venv/bin/python" ]]; then
  PYTHON=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
else
  die "no python interpreter found (.venv/bin/python or python3)"
fi

# ─── First-deploy detection ──────────────────────────────────────────────────
resolve_existing_agent_endpoint() {
  scripts/resolve-agent-endpoint.sh "$TARGET" 2>/dev/null || true
}

set_agent_endpoint_name() {
  AGENT_ENDPOINT_NAME="$1"
  if [[ -z "$AGENT_ENDPOINT_NAME" ]]; then
    die "Agent Bricks Supervisor endpoint name is empty"
  fi
  BUNDLE_VAR_FLAGS=("${VAR_FLAGS[@]}" --var "agent_endpoint_name=$AGENT_ENDPOINT_NAME")
  log "  using Agent Bricks Supervisor endpoint $AGENT_ENDPOINT_NAME"
}

run_agent_bricks_bootstrap() {
  local bootstrap_json endpoint
  bootstrap_json=$("$PYTHON" scripts/bootstrap_agent_bricks.py \
    --target "$TARGET" \
    --catalog "$DOCINTEL_CATALOG" \
    --schema "$DOCINTEL_SCHEMA" \
    --warehouse-id "$DOCINTEL_WAREHOUSE_ID" \
    --analyst-group "$ANALYST_GROUP") || \
    die "Agent Bricks bootstrap failed"
  endpoint=$(printf '%s' "$bootstrap_json" | "$PYTHON" -c "
import json, sys
payload = json.load(sys.stdin)
print(payload.get('supervisor_endpoint') or '')
")
  set_agent_endpoint_name "$endpoint"
}

# An existing Agent Bricks Supervisor means the generated serving endpoint can
# be resolved before app deployment. Treat absence as first deploy.
detect_mode() {
  if [[ "${DOCINTEL_FORCE_FIRST:-0}" == "1" ]]; then
    echo "first"
    return
  fi
  if [[ -n "$(resolve_existing_agent_endpoint)" ]]; then
    echo "steady"
    return
  fi
  echo "first"
}

MODE=$(detect_mode)
log "detected mode: $MODE"

# ─── Step 0: environment conflict checks (always run) ───────────────────────
log "step 0/6: checking environment conflicts"

# Lakebase soft-delete name conflict.
LAKEBASE_NAME=$("$PYTHON" -c "
import yaml, sys
with open('databricks.yml') as f:
    d = yaml.safe_load(f)
print(d['targets']['$TARGET']['variables']['lakebase_instance'])
" 2>/dev/null || echo "")
if [[ -n "$LAKEBASE_NAME" ]]; then
  if instances=$(databricks api get /api/2.0/database/instances --output json 2>/dev/null); then
    conflict=$("$PYTHON" -c "
import json, sys
d = json.loads(sys.argv[1])
target = sys.argv[2]
for i in d.get('database_instances', []):
    if i.get('name') == target and i.get('state') == 'DELETING':
        print('CONFLICT')
        break
" "$instances" "$LAKEBASE_NAME" 2>/dev/null || echo "")
    if [[ "$conflict" == "CONFLICT" ]]; then
      log "  → Lakebase name $LAKEBASE_NAME is in DELETING state; pick a different name in databricks.yml and retry"
      die "Lakebase name conflict (soft-delete retention)"
    fi
  fi
fi

wait_for_lakebase_available() {
  log "  waiting up to ${LAKEBASE_TIMEOUT}s for Lakebase $LAKEBASE_NAME state=AVAILABLE"
  deadline=$(( $(date +%s) + LAKEBASE_TIMEOUT ))
  while :; do
    state=$(databricks api get /api/2.0/database/instances --output json 2>/dev/null | \
      "$PYTHON" -c "
import json, sys
d = json.load(sys.stdin)
for i in d.get('database_instances', []):
    if i.get('name') == '$LAKEBASE_NAME':
        print(i.get('state', 'UNKNOWN'))
        break
" 2>/dev/null || echo "UNKNOWN")
    if [[ "$state" == "AVAILABLE" ]]; then
      log "  → Lakebase $LAKEBASE_NAME is AVAILABLE"
      return 0
    fi
    if (( $(date +%s) >= deadline )); then
      die "Lakebase $LAKEBASE_NAME did not reach AVAILABLE within ${LAKEBASE_TIMEOUT}s (state=$state)"
    fi
    sleep 15
  done
}

upload_samples() {
  log "  uploading synthetic samples to $VOLUME_PATH"
  shopt -s nullglob
  local sample_pdfs=(samples/*_10K_*.pdf)
  shopt -u nullglob
  if (( ${#sample_pdfs[@]} == 0 )); then
    log "    no PDFs in samples/; run samples/synthesize.py to regenerate"
    return
  fi
  for pdf in "${sample_pdfs[@]}"; do
    databricks fs cp "$pdf" "$VOLUME_PATH/" --overwrite >/dev/null 2>&1 || \
      log "    warn: fs cp $pdf failed, continuing"
  done
}

if [[ "$MODE" == "first" ]]; then
  # ─── First-deploy path: staged ─────────────────────────────────────────────
  log "step 1/6: stage-1 deploy (foundation only — first deploy)"

  # Hide consumer resources from the bundle. Trap restores on any exit.
  shopt -s nullglob
  consumer_files=(resources/consumers/*.yml)
  shopt -u nullglob

  restore_consumers() {
    local f
    for f in resources/consumers/*.yml.skip; do
      [[ -f "$f" ]] || continue
      mv "$f" "${f%.skip}"
    done
  }
  trap restore_consumers EXIT INT TERM

  for f in "${consumer_files[@]}"; do
    mv "$f" "$f.skip"
  done

  databricks bundle deploy -t "$TARGET" "${VAR_FLAGS[@]}" ${DEPLOY_FLAGS[@]+"${DEPLOY_FLAGS[@]}"} || \
    die "stage-1 deploy failed (foundation should be self-contained — investigate)"

  restore_consumers
  trap - EXIT INT TERM

  log "step 2/6: producing data"
  upload_samples
  databricks bundle run -t "$TARGET" "${VAR_FLAGS[@]}" "$PIPELINE_KEY" || \
    die "pipeline run failed — inspect SDP UI before retrying"
  "$PYTHON" scripts/wait_for_kpis.py --min-rows 1 --timeout "$WAIT_SECONDS" || \
    die "timed out waiting for $KPI_TABLE"

  # Materialize the VS index BEFORE Agent Bricks configuration so Knowledge
  # Assistant can attach the governed index as its knowledge source.
  log "  creating Vector Search index ${DOCINTEL_CATALOG}.${DOCINTEL_SCHEMA}.filings_summary_idx"
  "$PYTHON" jobs/index_refresh/sync_index.py \
    --endpoint "docintel-${TARGET}" \
    --index "${DOCINTEL_CATALOG}.${DOCINTEL_SCHEMA}.filings_summary_idx" \
    --source-table "${DOCINTEL_CATALOG}.${DOCINTEL_SCHEMA}.gold_filing_sections_indexable" \
    --primary-key section_uid \
    --embedding-endpoint "$EMBEDDING_ENDPOINT" || \
    die "VS index creation failed (sync_index.py)"

  run_agent_bricks_bootstrap
  wait_for_lakebase_available

  log "step 3/6: stage-2 deploy (full bundle — consumers join the foundation)"
  databricks bundle deploy -t "$TARGET" "${BUNDLE_VAR_FLAGS[@]}" ${DEPLOY_FLAGS[@]+"${DEPLOY_FLAGS[@]}"} || \
    die "stage-2 deploy failed; check logs"

  # The index_refresh job is created by stage-2 deploy and is `table_update`-
  # triggered. Triggers do not fire retroactively on the rows the pipeline
  # produced before the job existed, so run it once after deployment as an
  # idempotent smoke of the bundled job path.
  log "step 3.5/6: triggering initial Vector Search index materialization"
  databricks bundle run -t "$TARGET" "${BUNDLE_VAR_FLAGS[@]}" index_refresh || \
    log "  warn: index_refresh failed; the table_update trigger will retry on the next pipeline run"

else
  # ─── Steady-state path: single full deploy + in-place data refresh ────────
  set_agent_endpoint_name "$(resolve_existing_agent_endpoint)"
  log "step 1/6: full bundle deploy (steady-state — consumers already exist)"
  databricks bundle deploy -t "$TARGET" "${BUNDLE_VAR_FLAGS[@]}" ${DEPLOY_FLAGS[@]+"${DEPLOY_FLAGS[@]}"} || \
    die "bundle deploy failed; if a prior deploy was interrupted, set DOCINTEL_FORCE_LOCK=1 and retry"

  log "step 2/6: refreshing data + Agent Bricks configuration"
  upload_samples
  databricks bundle run -t "$TARGET" "${VAR_FLAGS[@]}" "$PIPELINE_KEY" || \
    die "pipeline run failed — inspect SDP UI before retrying"
  "$PYTHON" scripts/wait_for_kpis.py --min-rows 1 --timeout "$WAIT_SECONDS" || \
    die "timed out waiting for $KPI_TABLE"
  databricks bundle run -t "$TARGET" "${BUNDLE_VAR_FLAGS[@]}" index_refresh || \
    log "  warn: index_refresh failed; the table_update trigger will retry on the next pipeline run"
  run_agent_bricks_bootstrap

  log "step 3/6: skipped (no second deploy needed in steady-state)"
fi

# ─── Step 4: app run (both paths) ────────────────────────────────────────────
log "step 4/6: applying app config + restart"
databricks bundle run -t "$TARGET" "${BUNDLE_VAR_FLAGS[@]}" analyst_app || \
  log "  warn: analyst_app run failed; retry manually with 'databricks bundle run -t $TARGET analyst_app'"

# ─── Step 5: UC grants (idempotent) ──────────────────────────────────────────
log "step 5/6: applying UC grants for ${ANALYST_GROUP} (catalog → schema)"
databricks api patch \
  "/api/2.1/unity-catalog/permissions/CATALOG/${DOCINTEL_CATALOG}" \
  --json "{\"changes\":[{\"principal\":\"${ANALYST_GROUP}\",\"add\":[\"USE_CATALOG\"]}]}" \
  >/dev/null 2>&1 || log "  warn: USE_CATALOG grant failed (may already be applied; UC dedupes)"

databricks api patch \
  "/api/2.1/unity-catalog/permissions/SCHEMA/${DOCINTEL_CATALOG}.${DOCINTEL_SCHEMA}" \
  --json "{\"changes\":[{\"principal\":\"${ANALYST_GROUP}\",\"add\":[\"USE_SCHEMA\",\"SELECT\",\"EXECUTE\"]}]}" \
  >/dev/null 2>&1 || log "  warn: schema grants failed (may already be applied; UC dedupes)"

# OBO scope verification (only meaningful when user_api_scopes is declared).
if grep -q '^      user_api_scopes:' resources/consumers/analyst.app.yml 2>/dev/null; then
  log "  verifying OBO scopes on $APP_NAME"
  if app_state=$(databricks apps get "$APP_NAME" --output json 2>/dev/null); then
    "$PYTHON" -c "
import json
app = json.loads('''$app_state''')
scopes = set(app.get('user_api_scopes') or [])
required = {'serving.serving-endpoints', 'sql'}
missing = required - scopes
if missing:
    raise SystemExit(f'OBO scopes missing: {sorted(missing)} (got {sorted(scopes)})')
print(f'  OBO scopes intact: {sorted(scopes)}')
" || die "OBO scopes missing after deploy"
  else
    die "unable to read app state for OBO verification"
  fi
else
  die "resources/consumers/analyst.app.yml must declare user_api_scopes; OBO is mandatory"
fi

# ─── Step 6: smoke check ─────────────────────────────────────────────────────
log "step 6/6: smoke check on $AGENT_ENDPOINT_NAME"
if smoke=$("$PYTHON" -c "
from databricks.sdk import WorkspaceClient
from app.agent_bricks_client import invoke_agent_endpoint
import json, sys
w = WorkspaceClient()
payload = invoke_agent_endpoint(w, '$AGENT_ENDPOINT_NAME', 'What was ACMEs revenue in fiscal 2024?')
print(json.dumps({'endpoint': '$AGENT_ENDPOINT_NAME', 'keys': sorted(payload.keys())[:12]}))
" 2>&1); then
  log "  smoke OK: $smoke"
else
  log "  warn: smoke check failed (endpoint may still be warming up); details: $smoke"
fi

log "done."
log "  mode:        $MODE"
log "  endpoint:    $AGENT_ENDPOINT_NAME"
log "  KPI table:   $KPI_TABLE"
log "  app:         $APP_NAME"
log "  Lakebase:    $LAKEBASE_NAME"
log "next: $PYTHON evals/clears_eval.py --endpoint $AGENT_ENDPOINT_NAME --dataset evals/dataset.jsonl"
