#!/usr/bin/env bash
# Bootstrap a dev workspace end-to-end.
#
# Two modes, auto-detected:
#
#   FIRST DEPLOY (no serving endpoint yet)
#     resources/ has chicken-egg dependencies: consumers (serving endpoint,
#     monitor, app, lakebase catalog, vs endpoint) need foundation data
#     (registered model, populated KPI table, AVAILABLE Lakebase). DAB
#     deploys everything in one shot, so we stage:
#       1. Hide resources/consumers/*.yml → *.yml.skip; bundle deploy
#          touches only foundation. Trap restores on any exit.
#       2. Produce data: samples → pipeline → wait for KPIs → register
#          model → wait for Lakebase AVAILABLE.
#       3. Restore consumer YAMLs; bundle deploy full bundle. All deps
#          satisfied; consumers create cleanly.
#
#   STEADY STATE (consumers already exist)
#     The temp-rename trick is unsafe here: DAB tracks resource state and
#     would plan to DELETE any resource that disappears from config (per
#     Databricks bundle docs — removed config = removed workspace resource).
#     So in steady state we do a normal full bundle deploy and refresh data
#     in place: samples → pipeline → register a new model version → repoint
#     the serving endpoint via _promote_serving_endpoint.
#
# Common to both: bundle run analyst_app (apply config + restart),
# UC grants chain, smoke check.
#
# Required env vars:
#   DOCINTEL_CATALOG       e.g. workspace
#   DOCINTEL_SCHEMA        e.g. docintel_10k_dev
#   DOCINTEL_WAREHOUSE_ID  SQL warehouse id (used by wait_for_kpis + smoke)
#
# Optional:
#   DOCINTEL_TARGET            bundle target (default: dev)
#   DOCINTEL_ANALYST_GROUP     UC group for grants (default: "account users")
#   DOCINTEL_WAIT_SECONDS      poll timeout for KPI table (default: 600)
#   DOCINTEL_LAKEBASE_TIMEOUT  poll timeout for Lakebase (default: 600)
#   DOCINTEL_FORCE_FIRST       set to 1 to force the staged first-deploy path
#   DOCINTEL_FORCE_LOCK        set to 1 to pass --force-lock (use ONLY when a
#                              prior deploy crashed and left a stale lock —
#                              not a normal-flow flag).

set -euo pipefail

log() { echo "[bootstrap] $*" >&2; }
die() { log "error: $*"; exit 1; }

: "${DOCINTEL_CATALOG:?must be set (e.g. workspace)}"
: "${DOCINTEL_SCHEMA:?must be set (e.g. docintel_10k_dev)}"
: "${DOCINTEL_WAREHOUSE_ID:?must be set}"

TARGET="${DOCINTEL_TARGET:-dev}"
ANALYST_GROUP="${DOCINTEL_ANALYST_GROUP:-account users}"
WAIT_SECONDS="${DOCINTEL_WAIT_SECONDS:-600}"
LAKEBASE_TIMEOUT="${DOCINTEL_LAKEBASE_TIMEOUT:-600}"
ENDPOINT="analyst-agent-${TARGET}"
APP_NAME="doc-intel-analyst-${TARGET}"
KPI_TABLE="${DOCINTEL_CATALOG}.${DOCINTEL_SCHEMA}.gold_filing_kpis"
VOLUME_PATH="dbfs:/Volumes/${DOCINTEL_CATALOG}/${DOCINTEL_SCHEMA}/raw_filings"
PIPELINE_KEY="doc_intel_pipeline"

DEPLOY_FLAGS=()
if [[ "${DOCINTEL_FORCE_LOCK:-0}" == "1" ]]; then
  log "DOCINTEL_FORCE_LOCK=1 — passing --force-lock to bundle deploy (use only for stale-lock recovery)"
  DEPLOY_FLAGS+=(--force-lock)
fi

# Pin the bundle's `warehouse_id` variable to the user-selected ID so the
# dashboard + serving-endpoint env match wait_for_kpis / log_and_register.
# Without this, the bundle falls back to its `lookup: warehouse: Serverless
# Starter Warehouse` default — which fails validation in workspaces lacking
# that named warehouse, and silently picks a different ID otherwise.
VAR_FLAGS=(--var "warehouse_id=$DOCINTEL_WAREHOUSE_ID")

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
# A serving endpoint with a populated config means consumers were deployed
# previously (or the deploy got partway). Treat anything else as first deploy.
detect_mode() {
  if [[ "${DOCINTEL_FORCE_FIRST:-0}" == "1" ]]; then
    echo "first"
    return
  fi
  if ep_state=$(databricks api get "/api/2.0/serving-endpoints/${ENDPOINT}" --output json 2>/dev/null); then
    has_entity=$("$PYTHON" -c "
import json, sys
ep = json.loads(sys.argv[1])
served = (ep.get('config') or {}).get('served_entities') or (ep.get('config') or {}).get('served_models') or []
print('yes' if served else 'no')
" "$ep_state" 2>/dev/null || echo "no")
    if [[ "$has_entity" == "yes" ]]; then
      echo "steady"
      return
    fi
  fi
  echo "first"
}

MODE=$(detect_mode)
log "detected mode: $MODE"

# ─── Step 0: orphan detection + cleanup (always run) ────────────────────────
log "step 0/6: detecting orphans from prior failed runs"

# Malformed serving endpoint: exists but has no served entities.
if ep_state=$(databricks api get "/api/2.0/serving-endpoints/${ENDPOINT}" --output json 2>/dev/null); then
  if "$PYTHON" -c "
import json, sys
ep = json.loads(sys.argv[1])
served = (ep.get('config') or {}).get('served_entities') or (ep.get('config') or {}).get('served_models') or []
if not served:
    sys.exit(0)
sys.exit(1)
" "$ep_state" 2>/dev/null; then
    log "  → deleting malformed serving endpoint $ENDPOINT (no served entities)"
    databricks api delete "/api/2.0/serving-endpoints/${ENDPOINT}" >/dev/null 2>&1 || \
      log "  warn: delete failed, continuing"
  fi
fi

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

  databricks bundle deploy -t "$TARGET" "${VAR_FLAGS[@]}" "${DEPLOY_FLAGS[@]}" || \
    die "stage-1 deploy failed (foundation should be self-contained — investigate)"

  restore_consumers
  trap - EXIT INT TERM

  log "step 2/6: producing data"
  upload_samples
  databricks bundle run -t "$TARGET" "${VAR_FLAGS[@]}" "$PIPELINE_KEY" || \
    die "pipeline run failed — inspect SDP UI before retrying"
  "$PYTHON" scripts/wait_for_kpis.py --min-rows 1 --timeout "$WAIT_SECONDS" || \
    die "timed out waiting for $KPI_TABLE"
  "$PYTHON" agent/log_and_register.py --target "$TARGET" || \
    die "agent registration failed"
  wait_for_lakebase_available

  log "step 3/6: stage-2 deploy (full bundle — consumers join the foundation)"
  databricks bundle deploy -t "$TARGET" "${VAR_FLAGS[@]}" "${DEPLOY_FLAGS[@]}" || \
    die "stage-2 deploy failed; check logs"

  # The index_refresh job is created by stage-2 deploy and is `table_update`-
  # triggered. Triggers do not fire retroactively on the rows the pipeline
  # produced in stage 2, so we have to materialize the Vector Search index
  # explicitly the first time. sync_index.py is create-if-missing/sync-if-
  # exists, so this is idempotent on subsequent runs.
  log "step 3.5/6: triggering initial Vector Search index materialization"
  databricks bundle run -t "$TARGET" "${VAR_FLAGS[@]}" index_refresh || \
    log "  warn: index_refresh failed; the table_update trigger will retry on the next pipeline run"

else
  # ─── Steady-state path: single full deploy + in-place data refresh ────────
  log "step 1/6: full bundle deploy (steady-state — consumers already exist)"
  databricks bundle deploy -t "$TARGET" "${VAR_FLAGS[@]}" "${DEPLOY_FLAGS[@]}" || \
    die "bundle deploy failed; if a prior deploy was interrupted, set DOCINTEL_FORCE_LOCK=1 and retry"

  log "step 2/6: refreshing data + repointing serving endpoint"
  upload_samples
  databricks bundle run -t "$TARGET" "${VAR_FLAGS[@]}" "$PIPELINE_KEY" || \
    die "pipeline run failed — inspect SDP UI before retrying"
  "$PYTHON" scripts/wait_for_kpis.py --min-rows 1 --timeout "$WAIT_SECONDS" || \
    die "timed out waiting for $KPI_TABLE"
  # Register a new model version and update the served entity in-place.
  "$PYTHON" agent/log_and_register.py --target "$TARGET" --serving-endpoint "$ENDPOINT" || \
    die "agent registration failed"

  log "step 3/6: skipped (no second deploy needed in steady-state)"
fi

# ─── Step 4: app run (both paths) ────────────────────────────────────────────
log "step 4/6: applying app config + restart"
databricks bundle run -t "$TARGET" "${VAR_FLAGS[@]}" analyst_app || \
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
" || log "  warn: OBO scopes wiped — re-apply via 'databricks apps update $APP_NAME --user-api-scopes serving.serving-endpoints,sql,iam.access-control:read,iam.current-user:read'"
  fi
else
  log ""
  log "  ⚠ APP-LEVEL OBO IS OPERATIONALLY DISABLED"
  log "     resources/consumers/analyst.app.yml has user_api_scopes commented out, so:"
  log "       • Databricks Apps will NOT inject x-forwarded-access-token into requests."
  log "       • app/app.py:_user_client falls back to SP creds for every user."
  log "       • UC ACLs in the agent's downstream calls run as the app SP, not the user."
  log "     This is a deliberate fallback because the workspace lacks the user-token-"
  log "     passthrough feature. To enable OBO end-to-end:"
  log "       1. Workspace admin enables 'Databricks Apps - user token passthrough'."
  log "       2. Uncomment the user_api_scopes block in analyst.app.yml."
  log "       3. Re-deploy: databricks bundle deploy -t $TARGET && databricks bundle run -t $TARGET analyst_app"
  log ""
fi

# ─── Step 6: smoke check ─────────────────────────────────────────────────────
log "step 6/6: smoke check on $ENDPOINT"
if smoke=$("$PYTHON" -c "
from databricks.sdk import WorkspaceClient
import json, sys
w = WorkspaceClient()
out = w.serving_endpoints.query(name='$ENDPOINT', inputs=[{'question': 'What was ACMEs revenue in fiscal 2024?', 'top_k': 3}])
preds = out.predictions if hasattr(out, 'predictions') else out['predictions']
r = preds[0] if isinstance(preds, list) else preds
print(json.dumps({'grounded': r.get('grounded'), 'agent_path': r.get('agent_path'), 'citations': len(r.get('citations') or [])}))
" 2>&1); then
  log "  smoke OK: $smoke"
else
  log "  warn: smoke check failed (endpoint may still be warming up); details: $smoke"
fi

log "done."
log "  mode:        $MODE"
log "  endpoint:    $ENDPOINT"
log "  KPI table:   $KPI_TABLE"
log "  app:         $APP_NAME"
log "  Lakebase:    $LAKEBASE_NAME"
log "next: $PYTHON evals/clears_eval.py --endpoint $ENDPOINT --dataset evals/dataset.jsonl"
