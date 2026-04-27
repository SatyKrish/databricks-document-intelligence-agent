# Operating Runbook — 10-K Analyst

This runbook owns setup commands, deploy paths, configuration reference, and day-2 operations for demo/prod stacks. Architecture belongs in [`design.md`](./design.md); validation evidence belongs in [`../VALIDATION.md`](../VALIDATION.md).

## Prerequisites

- Python 3.11 or 3.12.
- Databricks CLI >= 0.298.
- A Databricks workspace with Serverless SQL, Unity Catalog, Document Intelligence AI Functions, Mosaic AI Vector Search, Agent Bricks, Databricks Apps, Lakebase, and Lakehouse Monitoring enabled.
- A serverless SQL warehouse ID.

Prod requires Databricks Apps user token passthrough. Demo can run with `app_obo_required=false` and App service-principal endpoint access.

## Deploy paths

Fresh demo workspace:

```bash
DOCINTEL_CATALOG=<catalog> \
DOCINTEL_SCHEMA=<schema> \
DOCINTEL_WAREHOUSE_ID=<warehouse-id> \
./scripts/bootstrap-demo.sh
```

The same script also handles steady-state demo deploys. It auto-detects whether the Agent Bricks Supervisor already exists and avoids deleting consumer resources on existing deployments.

App, YAML, pipeline, or job config changes after first bring-up:

```bash
AGENT_ENDPOINT_NAME="$(./scripts/resolve-agent-endpoint.sh demo)"
databricks bundle deploy -t demo --var "agent_endpoint_name=${AGENT_ENDPOINT_NAME}"
databricks bundle run -t demo --var "agent_endpoint_name=${AGENT_ENDPOINT_NAME}" analyst_app
```

Agent Bricks definition changes:

```bash
DOCINTEL_CATALOG=<catalog> \
DOCINTEL_SCHEMA=<schema> \
DOCINTEL_WAREHOUSE_ID=<warehouse-id> \
python -m agent.document_intelligence_agent --target demo

AGENT_ENDPOINT_NAME="$(./scripts/resolve-agent-endpoint.sh demo)"
databricks bundle deploy -t demo --var "agent_endpoint_name=${AGENT_ENDPOINT_NAME}"
databricks bundle run -t demo --var "agent_endpoint_name=${AGENT_ENDPOINT_NAME}" analyst_app
```

Pipeline SQL changes that must re-process existing filings:

```bash
databricks bundle run -t demo doc_intel_pipeline
```

Prod deploys must pass `service_principal_id` and use an OBO-enabled target:

```bash
TARGET=prod
AGENT_ENDPOINT_NAME="$(./scripts/resolve-agent-endpoint.sh "$TARGET")"
databricks bundle deploy -t "$TARGET" \
  --var service_principal_id=<sp-app-id> \
  --var "agent_endpoint_name=${AGENT_ENDPOINT_NAME}"
databricks bundle run -t "$TARGET" \
  --var service_principal_id=<sp-app-id> \
  --var "agent_endpoint_name=${AGENT_ENDPOINT_NAME}" \
  analyst_app
```

## Configuration reference

Bundle variables in `databricks.yml`:

| Variable | Default | Purpose |
|---|---|---|
| `catalog` | `workspace` | UC catalog for all resources |
| `schema` | `docintel_10k` / `docintel_10k_demo` | Schema under the catalog |
| `lakebase_instance` | per-target | Lakebase database instance name |
| `lakebase_stopped` | `false` | Set to `true` only after the instance exists |
| `service_principal_id` | `""` | Required for prod deploys |
| `warehouse_id` | lookup by name | Used by index refresh, dashboards, and KPI tool |
| `embedding_model_endpoint_name` | `databricks-bge-large-en` | Vector Search embeddings |
| `quality_threshold` | `22` | Section quality cutoff for index inclusion |
| `max_pdf_bytes` | `52428800` | Reject filings larger than 50 MB |
| `analyst_group` | `account users` | UC group for demo grants |
| `agent_endpoint_name` | `UNSET_AGENT_BRICKS_ENDPOINT` | Generated Supervisor endpoint resolved by `scripts/resolve-agent-endpoint.sh` |
| `app_obo_required` | `true` prod, `false` demo | Controls user-token passthrough requirement |

Bootstrap and CI environment variables:

| Variable | Required | Used by |
|---|---|---|
| `DOCINTEL_CATALOG` | yes | Bootstrap, CI, eval |
| `DOCINTEL_SCHEMA` | yes | Bootstrap, CI, eval |
| `DOCINTEL_WAREHOUSE_ID` | yes | Bootstrap, KPI polling, structured KPI tool |
| `DOCINTEL_TARGET` | no | Bootstrap target, defaults to `demo` |
| `DOCINTEL_ANALYST_GROUP` | no | UC grants, defaults to `account users` |
| `DOCINTEL_WAIT_SECONDS` | no | KPI-table poll timeout |
| `DOCINTEL_LAKEBASE_TIMEOUT` | no | Lakebase `AVAILABLE` poll timeout |
| `DATABRICKS_HOST` / `DATABRICKS_TOKEN` | CI only | GitHub Actions auth |

## Add a sample filing

1. `databricks fs cp <path>/<TICKER>_10K_<YEAR>.pdf dbfs:/Volumes/<catalog>/<schema>/raw_filings/`
2. Watch the pipeline: `databricks bundle run -t demo doc_intel_pipeline`
3. Verify:
   ```sql
   SELECT filename, company_name, fiscal_year, revenue
     FROM <catalog>.<schema>.gold_filing_kpis
    WHERE filename = '<filename>';
   ```

If the row never lands, check:
- `bronze_filings_rejected` — filings > 50 MB are dropped here.
- `silver_parsed_filings.parse_status` — `error` rows have a `parse_error` reason.
- The pipeline event log under the SDP UI.

## Debug a low quality_score

```sql
SELECT filename, section_seq, quality_score, quality_breakdown
  FROM <catalog>.<schema>.gold_filing_quality
 WHERE filename = '<filename>'
 ORDER BY section_seq;
```

`quality_breakdown` is a STRUCT of the 5 dimensions (each 0–6). Threshold for the index is **22/30**, set via `var.quality_threshold` in `databricks.yml`. To override per env, pass `--var quality_threshold=20` on deploy.

If a filing scores below threshold:
- It is retained in `gold_filing_sections` and `gold_filing_kpis` for audit (FR-005, SC-006).
- It is **excluded** from `gold_filing_sections_indexable` and therefore from Vector Search.

## Update Agent Bricks configuration

Agent Bricks resources are defined and applied by `agent/document_intelligence_agent.py`. Run it after changes to Knowledge Assistant instructions, Supervisor instructions, or the KPI tool function:

```bash
DOCINTEL_CATALOG=<catalog> \
DOCINTEL_SCHEMA=<schema> \
DOCINTEL_WAREHOUSE_ID=<warehouse-id> \
python -m agent.document_intelligence_agent --target demo
```

This creates or updates the Knowledge Assistant, syncs the Vector Search knowledge source, creates or updates the UC SQL KPI function, and wires both into the Supervisor Agent endpoint.

Agent Bricks generates concrete serving endpoint names. After applying the agent definition, always resolve the live Supervisor endpoint before deploying or restarting the app:

```bash
AGENT_ENDPOINT_NAME="$(./scripts/resolve-agent-endpoint.sh demo)"
databricks bundle deploy -t demo --var "agent_endpoint_name=${AGENT_ENDPOINT_NAME}"
databricks bundle run -t demo --var "agent_endpoint_name=${AGENT_ENDPOINT_NAME}" analyst_app
```

The app receives the generated endpoint as `DOCINTEL_AGENT_ENDPOINT`.

## Agent Bricks invocation and citations

The app and eval runner invoke the generated Supervisor endpoint through `POST /serving-endpoints/{endpoint}/invocations`. Prod uses the user's OBO token. Demo uses the App service principal when `DOCINTEL_OBO_REQUIRED=false`. They do not use `WorkspaceClient.serving_endpoints.query()` for Agent Bricks calls because workspace validation showed that path did not preserve the needed Agent Bricks response shape.

Current Agent Bricks output is an OpenAI Responses-style `output` message sequence. `app/agent_bricks_response.py` displays the last output text group as the final answer. Knowledge Assistant citations were observed during 2026-04-26 validation as markdown footnotes in intermediate messages, such as `[^p1]: ... _ACME_10K_2024.pdf_`; the app extracts filenames from those footnotes for citation chips. If citation chips show only `source`, capture a live payload and grep for `[^` and `.pdf_` to confirm whether the Knowledge Assistant citation format changed.

## Inspect CLEARS metrics in MLflow

CI resolves the generated Agent Bricks Supervisor serving endpoint, then runs `python evals/clears_eval.py --endpoint "$AGENT_ENDPOINT_NAME"` after each `demo` deploy. Look for the experiment `/Shared/docintel-clears-<user>`; each run logs:

- Per-axis metrics: `correctness`, `adherence`, `relevance`, `execution`, `safety`, `latency_p95_ms`
- Per-question latency: `latency_ms_<id>`
- Per-category slices: `p2_correctness`, `p3_correctness`, only when the active MLflow/databricks-agents output includes per-row correctness columns

Metric key names can vary across MLflow/databricks-agents versions. The eval runner maps current aggregate keys such as `correctness/percentage`, `guideline_adherence/percentage`, `groundedness/percentage`, and `safety/percentage` to the CLEARS axes. Failures are logged as a JSON list under the run tag `failures`. The script exit-code-fails the deploy if any threshold is missed (FR-010, SC-002, SC-003).

## Common failure modes

| Symptom | Likely cause | Action |
|---|---|---|
| `bundle validate` fails on `ai_parse_document` | Workspace lacks AI Functions GA | Move SQL warehouse to a recent serverless channel |
| Vector Search index sync stuck | Embedding endpoint not provisioned | Provision `databricks-bge-large-en` or override `var.embedding_model_endpoint_name` |
| `DOCINTEL_AGENT_ENDPOINT` is `UNSET_AGENT_BRICKS_ENDPOINT` | Bundle deploy/run omitted the generated endpoint variable | Re-run with `--var "agent_endpoint_name=$(./scripts/resolve-agent-endpoint.sh demo)"` |
| Agent endpoint 401 | Target auth mode does not have endpoint access | Demo: verify bootstrap/CI granted the App SP `CAN_QUERY` on the generated endpoint. Prod: verify `x-forwarded-access-token` is present and target `user_api_scopes` include `serving.serving-endpoints` and `sql` |
| App deploy fails with `Databricks Apps - user token passthrough feature is not enabled` | Prod target requires a workspace/org prerequisite | Enable Databricks Apps user-token passthrough and rerun. Demo should keep `app_obo_required=false` unless validating OBO |
| Agent answers ignore user UC permissions in prod | OBO scopes wiped by `bundle run` (documented destructive-update behavior — see [Databricks Apps deploy docs](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/deploy)) | Re-apply scopes to the target app: `databricks apps update <app-name> --user-api-scopes serving.serving-endpoints,sql,iam.access-control:read,iam.current-user:read` |
| Agent deployment cannot grant endpoint query permission | Permissions API was called with endpoint name instead of internal endpoint ID, or the generated endpoint is not ready | Use current `agent/document_intelligence_agent.py`; it waits for readiness and grants by serving endpoint ID |
| Streamlit user sees stale UC permissions | OBO token captured at WebSocket open; never refreshes ([Databricks Apps runtime docs](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/app-runtime)) | Reload the page after permission changes |
| Lakebase tables not writable from deployed App | Local-dev `streamlit run` initialised the `docintel_app` schema under user identity, not App SP | Connect as App SP and `DROP SCHEMA docintel_app CASCADE`; next App run re-creates it under SP. See `app/README.md` |
| CLEARS Latency axis fails | Agent Bricks orchestration or Knowledge Assistant source is too broad | Narrow the Knowledge Assistant source, tune Supervisor instructions, or reduce structured-tool fan-out |
| Citation chips render but filenames show `source` | Knowledge Assistant footnote format changed or omitted filename markers | Capture the raw Agent Bricks payload and compare it with `app/agent_bricks_response.py`'s markdown-footnote parser |
| App errors connecting to Lakebase | Database resource binding missing connection fields, OAuth credential minting failed, or App SP lacks Lakebase instance `CAN_USE` | Check the `docintel-lakebase` resource binding plus `PGHOST`/`PGPORT`/`PGUSER`/`PGDATABASE`/`DOCINTEL_LAKEBASE_INSTANCE`/`DOCINTEL_LAKEBASE_SCHEMA` in the App runtime. `PGPASSWORD` is minted at connection time by `app/lakebase_client.py` |

## Verifying end-to-end OBO

1. **Workspace admin** enables the "Databricks Apps - user token passthrough" feature in workspace settings.
2. Confirm the required scopes are declared on the prod target in `databricks.yml`:
   ```yaml
   user_api_scopes:
     - serving.serving-endpoints     # invoke Agent Bricks endpoint as user
     - sql                            # structured KPI tool runs UC SQL
     - iam.access-control:read        # default
     - iam.current-user:read          # default
   ```

Demo uses `app_obo_required=false` unless overridden; the bootstrap grants the App service principal `CAN_QUERY` on the generated Supervisor endpoint.

3. Redeploy the OBO-enabled target:
   ```bash
   TARGET=prod
   AGENT_ENDPOINT_NAME="$(./scripts/resolve-agent-endpoint.sh "$TARGET")"
   databricks bundle deploy -t "$TARGET" --var "agent_endpoint_name=${AGENT_ENDPOINT_NAME}"
   databricks bundle run -t "$TARGET" --var "agent_endpoint_name=${AGENT_ENDPOINT_NAME}" analyst_app
   ```
4. Verify: bootstrap scope checks assert required scopes. Visit the deployed app, ask a question, and confirm in audit logs that Agent Bricks, Knowledge Assistant, and structured KPI SQL calls run under the invoking user's identity.

## CLEARS thresholds

Defined in `evals/clears_eval.py` and pinned by `spec.md` FR-010 / Constitution Principle V:

| Axis | Threshold | Source |
|---|---|---|
| Correctness | ≥ 0.80 | spec FR-010 |
| Latency p95 | ≤ 8000 ms | SC-009 |
| Execution | ≥ 0.95 | FR-010 |
| Adherence | ≥ 0.90 | FR-010 |
| Relevance | ≥ 0.80 | FR-010 |
| Safety | ≥ 0.99 | FR-010 |
| P2 correctness slice | ≥ 0.80 | SC-002 |
| P3 correctness slice | ≥ 0.70 | SC-003 |

Changing any threshold requires a constitution amendment per the Governance section of `.specify/memory/constitution.md`.

## v1 baseline

No passing v1 baseline has been recorded yet. Latest demo evidence as of 2026-04-26:

```
MLflow run ID:       772e902cab92459f9bf569296fc5f801
Deployed at:         2026-04-26
Correctness:         0.323
Adherence:           0.000
Relevance/grounding: 0.516
Safety:              1.000
Execution:           1.000
Latency p95:         31711 ms
P2/P3 slices:        unavailable in the current aggregate metric output
```

Do not promote this as reference-ready until CLEARS passes. Do not promote to prod until Databricks Apps user-token passthrough is enabled and OBO audit evidence is captured in the target workspace.

## Known deploy ordering gaps

The bundle has three chicken-egg dependencies that a single `bundle deploy` cannot
resolve on a fresh workspace. Each needs a phase-2 step after a prior side effect:

1. **Databricks App needs the generated Agent Bricks endpoint name**
   - Agent Bricks generates concrete Knowledge Assistant and Supervisor serving
     endpoint names.
   - `agent/document_intelligence_agent.py` returns the generated Supervisor
     endpoint, and `resources/consumers/analyst.app.yml` injects it into
     `DOCINTEL_AGENT_ENDPOINT` via the `agent_endpoint_name` bundle variable.
   - **Fix**: bootstrap creates data and Agent Bricks resources before the full
     consumer deploy.

2. **Lakehouse Monitor references `gold_filing_kpis` which the pipeline must create first**
   - `resources/consumers/kpi_drift.yml` attaches to a table that doesn't exist
     until the pipeline has run at least once.
   - **Fix**: stage the first deploy so the pipeline runs before consumers are
     reconciled.

3. **Lakebase `database_catalog` and `App` race the `database_instance` provisioning**
   - The catalog and app attach to the instance before the instance has finished
     coming up. Re-running `bundle deploy` immediately after the first attempt
     usually succeeds since the instance is then ready.
   - **Fix**: bootstrap waits for Lakebase to reach `AVAILABLE` before the full
     consumer deploy.

The script implements a **staged deploy**: resources are split into
`resources/foundation/` (no data deps) and `resources/consumers/` (need
data). Stage 1 temporarily renames consumer YAMLs to `*.yml.skip` so the
bundle's `resources/**/*.yml` glob excludes them — foundation deploys
cleanly. Stage 2 brings up data (sample upload, pipeline run, VS index
materialization, Agent Bricks bootstrap, Lakebase ready) and then runs full `bundle deploy`, with all
consumer dependencies satisfied. The previous "errors tolerated on first
deploy" workaround is gone — both deploys succeed cleanly.

Six-step flow:

1. **Environment conflict checks** — fail loudly if
   the configured Lakebase name is in `DELETING` state (soft-delete
   retention conflict — bump the suffix and retry).
2. **Foundation deploy** — `resources/consumers/*.yml` renamed to
   `*.yml.skip`; `bundle deploy` only touches catalog/schema/volume,
   pipeline, retention job, Lakebase instance, Vector Search endpoint.
3. **Produce data** — upload synthetic samples, run pipeline, wait for
   `gold_filing_kpis`, materialize the Vector Search index, bootstrap
   Agent Bricks Knowledge Assistant + Supervisor Agent,
   wait for Lakebase to reach `AVAILABLE`.
4. **Consumer deploy** — full `bundle deploy` (foundation idempotent;
   consumers create cleanly because all deps are live).
5. **App run + UC grants chain** — `bundle run analyst_app`,
   `USE_CATALOG → USE_SCHEMA → SELECT/EXECUTE` for the analyst group.
6. **Smoke check** — query the Agent Bricks Supervisor endpoint with one sample question.

CI (`.github/workflows/deploy.yml`) uses the same staged shape for steady-
state pushes.
