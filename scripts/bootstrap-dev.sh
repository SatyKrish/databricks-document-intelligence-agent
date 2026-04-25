#!/usr/bin/env bash
# Bootstrap a fresh dev workspace end-to-end via STAGED deploys.
#
# Architecture: resources are split into two stages (resources/foundation/
# and resources/consumers/). Foundation has no data dependencies; consumers
# need foundation to be running and producing data (registered model version,
# populated KPI table, AVAILABLE Lakebase instance).
#
# DAB doesn't natively support partial deploys, so stage 1 temporarily renames
# resources/consumers/*.yml → *.yml.skip so the bundle's `resources/**/*.yml`
# glob skips them. A trap restores the names on any exit path. Stage 2 deploys
# the full bundle (foundation idempotent, consumers create cleanly).
#
# Steps:
#   0. Detect & clean known orphans from prior failed runs.
#   1. Stage 1 deploy: foundation only.
#   2. Produce data: upload samples, run pipeline, register model, wait for
#      Lakebase to be AVAILABLE.
#   3. Stage 2 deploy: full bundle (consumers attach to live foundation).
#   4. Apply app config + restart.
#   5. UC grants chain (USE_CATALOG → USE_SCHEMA → SELECT/EXECUTE).
#   6. Smoke check.
#
# Required env vars:
#   DOCINTEL_CATALOG       e.g. workspace
#   DOCINTEL_SCHEMA        e.g. docintel_10k_dev
#   DOCINTEL_WAREHOUSE_ID  SQL warehouse id (used by wait_for_kpis + smoke)
#
# Optional:
#   DOCINTEL_TARGET           bundle target (default: dev)
#   DOCINTEL_ANALYST_GROUP    UC group for grants (default: "account users")
#   DOCINTEL_WAIT_SECONDS     poll timeout for KPI table (default: 600)
#   DOCINTEL_LAKEBASE_TIMEOUT poll timeout for Lakebase (default: 600)

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

# ─── Step 0: orphan detection + cleanup ─────────────────────────────────────
# Remove leftover consumer resources from prior partial runs that would block
# stage 2's clean creates with RESOURCE_ALREADY_EXISTS. Strict checks: only
# delete if the resource is in a known-broken state.
log "step 0/6: detecting orphans from prior failed runs"

# Malformed serving endpoint: exists but has no served entities (created in a
# stage-1 deploy that ran before the model version existed).
if ep_state=$(databricks api get "/api/2.0/serving-endpoints/${ENDPOINT}" --output json 2>/dev/null); then
  if "$PYTHON" -c "
import json, sys
ep = json.loads(sys.argv[1])
served = (ep.get('config') or {}).get('served_entities') or (ep.get('config') or {}).get('served_models') or []
if not served:
    print('orphan: empty config', file=sys.stderr); sys.exit(0)
sys.exit(1)
" "$ep_state" 2>/dev/null; then
    log "  → deleting malformed serving endpoint $ENDPOINT (no served entities)"
    databricks api delete "/api/2.0/serving-endpoints/${ENDPOINT}" >/dev/null 2>&1 || \
      log "  warn: delete failed, continuing"
  fi
fi

# Lakebase soft-delete name conflict: if the desired instance name appears in
# DELETING state, bump var.lakebase_instance to the next free suffix.
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

# ─── Step 1: stage-1 deploy (foundation only) ───────────────────────────────
log "step 1/6: stage-1 deploy (foundation only)"

# Hide consumer resources from the bundle by suffixing them. Trap restores
# them on any exit path so a crash here doesn't leave the repo in a half-
# renamed state.
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

databricks bundle deploy -t "$TARGET" --force-lock || \
  die "stage-1 deploy failed (foundation should be self-contained — investigate)"

# Restore consumer YAMLs so stage 2 can deploy them.
restore_consumers
trap - EXIT INT TERM

# ─── Step 2: produce data ───────────────────────────────────────────────────
log "step 2/6: producing data — uploading samples, running pipeline, registering model"

shopt -s nullglob
sample_pdfs=(samples/*_10K_*.pdf)
shopt -u nullglob
if (( ${#sample_pdfs[@]} == 0 )); then
  log "  no PDFs in samples/; run samples/synthesize.py to regenerate"
else
  for pdf in "${sample_pdfs[@]}"; do
    databricks fs cp "$pdf" "$VOLUME_PATH/" --overwrite >/dev/null 2>&1 || \
      log "  warn: fs cp $pdf failed, continuing"
  done
fi

databricks bundle run -t "$TARGET" "$PIPELINE_KEY" || \
  die "pipeline run failed — inspect SDP UI before retrying"

"$PYTHON" scripts/wait_for_kpis.py --min-rows 1 --timeout "$WAIT_SECONDS" || \
  die "timed out waiting for $KPI_TABLE"

# Register the agent model (no --serving-endpoint — endpoint doesn't exist
# until stage 2 deploy).
"$PYTHON" agent/log_and_register.py --target "$TARGET" || \
  die "agent registration failed"

# Wait for Lakebase to reach AVAILABLE so stage 2's catalog/app bind cleanly.
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
    break
  fi
  if (( $(date +%s) >= deadline )); then
    die "Lakebase $LAKEBASE_NAME did not reach AVAILABLE within ${LAKEBASE_TIMEOUT}s (state=$state)"
  fi
  sleep 15
done

# ─── Step 3: stage-2 deploy (full bundle) ───────────────────────────────────
log "step 3/6: stage-2 deploy (full bundle — consumers join the foundation)"
databricks bundle deploy -t "$TARGET" --force-lock || \
  die "stage-2 deploy failed — should not happen if data is in place; check logs"

# ─── Step 4: app run ────────────────────────────────────────────────────────
log "step 4/6: applying app config + restart"
databricks bundle run -t "$TARGET" analyst_app || \
  log "  warn: analyst_app run failed; retry manually with 'databricks bundle run -t $TARGET analyst_app'"

# ─── Step 5: UC grants chain ────────────────────────────────────────────────
log "step 5/6: applying UC grants for ${ANALYST_GROUP} (catalog → schema)"
databricks api patch \
  "/api/2.1/unity-catalog/permissions/CATALOG/${DOCINTEL_CATALOG}" \
  --json "{\"changes\":[{\"principal\":\"${ANALYST_GROUP}\",\"add\":[\"USE_CATALOG\"]}]}" \
  >/dev/null 2>&1 || log "  warn: USE_CATALOG grant failed (may already be applied; UC dedupes)"

databricks api patch \
  "/api/2.1/unity-catalog/permissions/SCHEMA/${DOCINTEL_CATALOG}.${DOCINTEL_SCHEMA}" \
  --json "{\"changes\":[{\"principal\":\"${ANALYST_GROUP}\",\"add\":[\"USE_SCHEMA\",\"SELECT\",\"EXECUTE\"]}]}" \
  >/dev/null 2>&1 || log "  warn: schema grants failed (may already be applied; UC dedupes)"

# Optional OBO scope verification: only if user_api_scopes is declared (the
# workspace must have user-token-passthrough enabled).
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

# ─── Step 6: smoke check ────────────────────────────────────────────────────
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
log "  endpoint:    $ENDPOINT"
log "  KPI table:   $KPI_TABLE"
log "  app:         $APP_NAME"
log "  Lakebase:    $LAKEBASE_NAME"
log "next: $PYTHON evals/clears_eval.py --endpoint $ENDPOINT --dataset evals/dataset.jsonl"
