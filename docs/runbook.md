# Operating Runbook — 10-K Analyst

This runbook covers day-2 operations for the deployed demo/prod stacks. For first-time setup follow [`specs/001-doc-intel-10k/quickstart.md`](../specs/001-doc-intel-10k/quickstart.md).

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

Agent Bricks resources are managed by `scripts/bootstrap_agent_bricks.py`. Run it after changes to Knowledge Assistant instructions, Supervisor instructions, or the KPI tool function:

```bash
DOCINTEL_CATALOG=<catalog> \
DOCINTEL_SCHEMA=<schema> \
DOCINTEL_WAREHOUSE_ID=<warehouse-id> \
python scripts/bootstrap_agent_bricks.py --target demo
```

This creates or updates the Knowledge Assistant, syncs the Vector Search knowledge source, creates or updates the UC SQL KPI function, and wires both into the Supervisor Agent endpoint.

## Inspect CLEARS metrics in MLflow

CI resolves the generated Agent Bricks Supervisor serving endpoint, then runs `python evals/clears_eval.py --endpoint "$AGENT_ENDPOINT_NAME"` after each `demo` deploy. Look for the experiment `/Shared/docintel-clears-<user>`; each run logs:

- Per-axis metrics: `correctness`, `adherence`, `relevance`, `execution`, `safety`, `latency_p95_ms`
- Per-category slices: `p2_correctness`, `p3_correctness`
- Per-question latency: `latency_ms_<id>`

Failures are logged as a JSON list under the run tag `failures`. The script exit-code-fails the deploy if any threshold is missed (FR-010, SC-002, SC-003).

## Common failure modes

| Symptom | Likely cause | Action |
|---|---|---|
| `bundle validate` fails on `ai_parse_document` | Workspace lacks AI Functions GA | Move SQL warehouse to a recent serverless channel |
| Vector Search index sync stuck | Embedding endpoint not provisioned | Provision `databricks-bge-large-en` or override `var.embedding_model_endpoint_name` |
| Agent endpoint 401 | OBO not plumbed end-to-end | Verify `app/app.py:_user_client` reads `x-forwarded-access-token` and `resources/consumers/analyst.app.yml:user_api_scopes` includes `serving.serving-endpoints` and `sql` |
| Agent answers ignore user UC permissions | OBO scopes wiped by `bundle run` (documented destructive-update behavior — see [Databricks Apps deploy docs](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/deploy)) | Re-apply: `databricks apps update doc-intel-analyst-demo --user-api-scopes serving.serving-endpoints,sql,iam.access-control:read,iam.current-user:read` |
| Streamlit user sees stale UC permissions | OBO token captured at WebSocket open; never refreshes ([Databricks Apps runtime docs](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/app-runtime)) | Reload the page after permission changes |
| Lakebase tables not writable from deployed App | Local-dev `streamlit run` initialised schema under user identity, not App SP | Connect as App SP and `DROP TABLE feedback, query_logs, conversation_history`; next App run re-creates them under SP. See `app/README.md` |
| CLEARS Latency axis fails | Agent Bricks orchestration or Knowledge Assistant source is too broad | Narrow the Knowledge Assistant source, tune Supervisor instructions, or reduce structured-tool fan-out |
| App errors connecting to Lakebase | Database resource binding missing Postgres env vars | Check the `docintel-lakebase` resource binding and `PGHOST`/`PGPORT`/`PGUSER`/`PGPASSWORD`/`PGDATABASE` in the App runtime |

## Verifying end-to-end OBO

Databricks Apps user-token passthrough, Agent Bricks OBO, AI Gateway identity enforcement, and UC grants are production prerequisites. Bootstrap must fail if any required scope or workspace feature is missing.

To verify OBO end-to-end:

1. **Workspace admin** enables the "Databricks Apps - user token passthrough" feature in workspace settings.
2. Confirm the `user_api_scopes` block in `resources/consumers/analyst.app.yml` is present. Required scopes for the analyst app's call chain:
   ```yaml
   user_api_scopes:
     - serving.serving-endpoints     # invoke Agent Bricks endpoint as user
     - sql                            # structured KPI tool runs UC SQL
     - iam.access-control:read        # default
     - iam.current-user:read          # default
   ```
3. Redeploy: `databricks bundle deploy -t demo && databricks bundle run -t demo analyst_app`.
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

(populate after the first successful `demo` deploy)

```
MLflow run ID:   <fill in>
Deployed at:     <date>
P2 correctness:  <value>
P3 correctness:  <value>
Latency p95:     <ms>
```

## Known deploy ordering gaps

The bundle has three chicken-egg dependencies that a single `bundle deploy` cannot
resolve on a fresh workspace. Each needs a phase-2 step after a prior side effect:

1. **Databricks App binds to an Agent Bricks endpoint**
   - Agent Bricks generates concrete Knowledge Assistant and Supervisor serving
     endpoint names.
   - `scripts/bootstrap_agent_bricks.py` returns the generated Supervisor
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

A clean fresh-workspace bring-up is a single command:

```bash
DOCINTEL_CATALOG=<catalog> \
DOCINTEL_SCHEMA=<schema> \
DOCINTEL_WAREHOUSE_ID=<warehouse-id> \
./scripts/bootstrap-demo.sh
```

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
