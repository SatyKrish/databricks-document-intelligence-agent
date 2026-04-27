# Validation Guide — Databricks Document Intelligence Agent

Use this guide to prove the reference implementation works in a Databricks workspace.

## Local Static Checks

```bash
python3 -m py_compile \
  agent/tools.py \
  app/app.py app/agent_bricks_client.py app/agent_bricks_response.py app/lakebase_client.py \
  evals/clears_eval.py agent/document_intelligence_agent.py \
  scripts/wait_for_kpis.py samples/synthesize.py

bash -n scripts/bootstrap-demo.sh
pytest agent/tests
databricks bundle validate --strict -t demo
```

Expected prod safety check:

```bash
databricks bundle validate --strict -t prod
```

This should fail unless `service_principal_id` is provided.

## Fresh Demo Bring-Up

```bash
export DOCINTEL_CATALOG=workspace
export DOCINTEL_SCHEMA=docintel_10k_demo
export DOCINTEL_WAREHOUSE_ID=<warehouse-id>

./scripts/bootstrap-demo.sh
```

Expected outcomes:

- Foundation resources deploy first.
- Synthetic PDFs upload to the `raw_filings` volume.
- Pipeline creates Gold rows.
- Agent Bricks Knowledge Assistant and Supervisor Agent are created or updated.
- Consumer resources deploy cleanly.
- App config is applied with `bundle run analyst_app`, including `DOCINTEL_AGENT_ENDPOINT` set from the generated Supervisor endpoint name.
- Bootstrap verifies the target auth mode. Demo leaves `user_api_scopes` unset and grants the App service principal `CAN_QUERY`; prod requires OBO scopes.
- Smoke query reaches the Agent Bricks supervisor endpoint.

If a prod app deploy fails with `Databricks Apps - user token passthrough feature is not enabled`, enable the workspace/org feature and rerun. Demo uses `app_obo_required=false` by default for workspaces where user-token passthrough is not enabled.

## Data Checks

```sql
SELECT filename, company_name, fiscal_year, revenue, ebitda
FROM <catalog>.<schema>.gold_filing_kpis
ORDER BY filename;

SELECT filename, section_seq, section_label, quality_score
FROM <catalog>.<schema>.gold_filing_sections_indexable
ORDER BY filename, section_seq;
```

Expected:

- ACME, BETA, and GAMMA have KPI rows.
- `garbage_10K_2024.pdf` does not appear in the indexable table.

## Agent And Eval Checks

```bash
python evals/clears_eval.py \
  --endpoint "$(./scripts/resolve-agent-endpoint.sh demo)" \
  --dataset evals/dataset.jsonl
```

Expected:

- Correctness, adherence, relevance, execution, safety, and latency thresholds pass.
- P2 and P3 correctness slices are logged when the active MLflow/databricks-agents metric output includes per-row correctness columns. Current 1.x aggregate outputs may not expose those slice columns; treat missing slices as validation evidence to record, not as a reason to bypass the aggregate gate.
- No citations reference `garbage_10K_2024.pdf`.

## App Checks

- Open `doc-intel-analyst-demo`.
- Ask: `What were the top 3 risk factors disclosed by ACME in their FY24 10-K?`
- Confirm the response has citation chips and the turn is written to Lakebase.
- Ask: `What was ACME's revenue in fiscal year 2024?` to verify the structured KPI tool path.
- Submit thumbs feedback and confirm a feedback row is written.

## App Auth Verification

- Demo: confirm `user_api_scopes` is unset and `DOCINTEL_OBO_REQUIRED=false`.
- Prod: confirm `user_api_scopes` is present and `DOCINTEL_OBO_REQUIRED=true`.
- Run for the target being verified:
  ```bash
  TARGET=demo
  AGENT_ENDPOINT_NAME="$(./scripts/resolve-agent-endpoint.sh "$TARGET")"
  databricks bundle deploy -t "$TARGET" --var "agent_endpoint_name=${AGENT_ENDPOINT_NAME}"
  databricks bundle run -t "$TARGET" --var "agent_endpoint_name=${AGENT_ENDPOINT_NAME}" analyst_app
  ```
- Confirm bootstrap or CI verifies the target auth mode. Demo grants the App SP endpoint access; prod verifies `serving.serving-endpoints` and `sql` scopes.
- For prod, check audit logs for user-scoped downstream access through Agent Bricks, Knowledge Assistant, and the structured KPI SQL function.
- If prod cannot grant user-token passthrough, deployment is invalid and must fail.

## Latest Demo Snapshot

As of 2026-04-26, the demo workspace evidence is:

- Bundle validation passed with the resolved Agent Bricks Supervisor endpoint.
- Document Intelligence Agent deployment succeeded:
  - Knowledge Assistant display name: `doc-intel-knowledge-demo`
  - Supervisor display name: `doc-intel-supervisor-demo`
  - UC function: `workspace.docintel_10k_demo.lookup_10k_kpis`
- Direct Supervisor endpoint smoke passed. The ACME FY2024 revenue question returned `$94.2 billion` and referenced `ACME_10K_2024.pdf` through the structured KPI path.
- Databricks App deploy succeeded in demo App-SP mode:
  - App: `doc-intel-analyst-demo`
  - Endpoint: `mas-dc6aba10-endpoint`
  - `DOCINTEL_OBO_REQUIRED=false`
  - `user_api_scopes` unset
  - App service principal granted `CAN_QUERY` on the generated Supervisor endpoint.
- Lakebase OAuth credential handling was validated without `PGPASSWORD`; the deployed app code mints the database password at connection time.
- Vector Search `index_refresh` job rerun terminated `SUCCESS`.
- CLEARS live eval completed but failed the configured gate:
  - MLflow run ID: `772e902cab92459f9bf569296fc5f801`
  - correctness: `0.323`
  - adherence: `0.000`
  - relevance/groundedness: `0.516`
  - safety: `1.000`
  - execution: `1.000`
  - latency p95: `31711ms`

Status: Agent Bricks deployment mechanics, demo App deploy, Lakebase OAuth credential handling, and direct serving smoke passed. Reference-ready quality remains open until CLEARS passes. Prod OBO readiness remains open until validated in a workspace with user-token passthrough enabled.
