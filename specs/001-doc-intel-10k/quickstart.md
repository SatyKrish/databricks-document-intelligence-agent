# Quickstart: Deploy and Test the 10-K Analyst

Goal: from a clean clone, stand up the entire stack on the Databricks `demo` target and run the P1, P2, and P3 acceptance checks in 15–25 minutes.

## Prerequisites

- macOS or Linux, `python` 3.11+, `git`, `databricks` CLI ≥ 0.298 (`brew install databricks/tap/databricks`)
- A Databricks workspace with: serverless SQL warehouse (AI Functions GA), Mosaic AI Vector Search, Agent Bricks Knowledge Assistant and Supervisor Agent, AI Gateway, Databricks Apps, Unity Catalog, and Lakebase enabled. Prod also requires Databricks Apps user-token passthrough.
- An auth profile (`databricks auth login --host <workspace-url>` once); verify with `databricks auth profiles`
- Local virtualenv: `python -m venv .venv && .venv/bin/pip install -r agent/requirements.txt -r evals/requirements.txt`

## 1. Configure the bundle

The `demo` target's defaults (in `databricks.yml`) are `catalog=workspace`, `schema=docintel_10k_demo`. Override per the workspace via env vars or `--var`:

```bash
cd databricks
databricks bundle validate --strict -t demo
```

If validate prints no errors, every resource YAML is schema-correct.

## 2. Stand up demo (staged deploy)

```bash
DOCINTEL_CATALOG=workspace \
DOCINTEL_SCHEMA=docintel_10k_demo \
DOCINTEL_WAREHOUSE_ID=<your-warehouse-id> \
./scripts/bootstrap-demo.sh
```

The script implements a 6-step staged deploy:

1. Check environment conflicts such as Lakebase soft-delete name retention.
2. **Stage 1**: deploy `resources/foundation/` only (catalog/schema/volume, pipeline, retention job, Lakebase instance, VS endpoint) — consumer YAMLs are temp-renamed to `*.yml.skip`.
3. **Produce data**: upload synthetic samples, run pipeline, materialize the VS index, configure Agent Bricks Knowledge Assistant + Supervisor Agent, wait for Lakebase to reach `AVAILABLE`.
4. **Stage 2**: full `bundle deploy` — consumers (monitor, index-refresh job, app, dashboard, Lakebase catalog) attach to the live foundation. The VS endpoint is deployed in stage 1, and the bootstrap materializes the VS index before Knowledge Assistant configuration.
5. `bundle run analyst_app`; UC grants chain (`USE_CATALOG → USE_SCHEMA → SELECT/EXECUTE`).
6. Smoke check on the analyst endpoint.

Total time on a fresh workspace: 15–25 minutes.

## 3. Verify P1 — ingest, parse, extract

The bootstrap script already uploaded `samples/{ACME,BETA,GAMMA,garbage}_10K_2024.pdf` and waited for the pipeline. Verify the KPI table:

```sql
SELECT filename, company_name, fiscal_year, revenue, ebitda,
       size(top_risks) AS num_risks,
       size(segment_revenue) AS num_segments
  FROM workspace.docintel_10k_demo.gold_filing_kpis
 ORDER BY filename;
```

Expect 4 rows. ACME/BETA/GAMMA each show non-null revenue (`94.2`, `212.0`, `305.0` $B), non-null ebitda, ≥5 `top_risks`, 3+ `segment_revenue` entries (typed `ARRAY<STRUCT<name STRING, revenue DECIMAL>>`). The `garbage_10K_2024.pdf` row exists but its `quality_score` (visible in `gold_filing_quality`) is below the 22 threshold, so it's excluded from the Vector Search index. Confirms SC-001, SC-006, SC-008.

## 4. Verify P2 — ask the corpus

Open the deployed App URL (workspace UI → Apps → `doc-intel-analyst-demo`). Ask:

> What were the top 3 risk factors disclosed by ACME in their FY24 10-K?

Expect: a grounded answer naming ≥3 risks (macroeconomic conditions, competitive pressure in AI, supply chain concentration), each with a citation chip linking back to `ACME_10K_2024.pdf` / `Risk`. Submit thumbs-up; refresh; the feedback row appears in `lakebase.feedback`. Confirms SC-002, SC-007.

To bring real EDGAR filings online instead of the synthetic samples, see `samples/README.md` — the volume accepts any `*_10K_*.pdf` and the pipeline reacts via Auto Loader (`continuous: true` in prod, `false` in demo).

## 5. Verify P3 — cross-company

The bootstrap already loaded BETA and GAMMA, so cross-company is ready. Ask in the App:

> Compare segment revenue between ACME, BETA, and GAMMA in their most recent 10-Ks.

Expect: a markdown table with one row per company, segment-revenue values matching `gold_filing_kpis.segment_revenue`, citations pointing at each filing. Confirms SC-003.

## 6. Run CLEARS evaluation

```bash
DOCINTEL_CATALOG=workspace \
DOCINTEL_SCHEMA=docintel_10k_demo \
.venv/bin/python evals/clears_eval.py \
  --endpoint "$(./scripts/resolve-agent-endpoint.sh demo)" \
  --dataset evals/dataset.jsonl
```

Exit 0 iff every CLEARS axis meets thresholds (C≥0.8, L p95≤8s, E≥0.95, A≥0.9, R≥0.8, S≥0.99). The script uses `mlflow.evaluate(model_type="databricks-agent")` for the LLM-judged axes (correctness, guideline_adherence, chunk_relevance, safety) and computes Execution + Latency from the raw response stream. SC-002 (P2 ≥0.80) and SC-003 (P3 ≥0.70) are sliced from `result.tables['eval_results']`. Confirms FR-010 / SC-002 / SC-003.

## 7. Tear down

```bash
databricks bundle destroy -t demo --auto-approve
```

Note: the Lakebase instance enters a soft-delete state for ~7 days during which its name is reserved. To redeploy quickly, bump `lakebase_instance` in `databricks.yml` (e.g., `docintel-demo-state-v4`) before re-running the bootstrap.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `bundle validate` errors on `ai_parse_document` | Workspace lacks AI Functions GA | Move SQL warehouse to a recent serverless channel |
| Vector Search index sync stuck | Embedding endpoint not provisioned | Provision `databricks-bge-large-en` or override `var.embedding_model_endpoint_name` |
| Agent endpoint 401 from App | Target auth mode does not have endpoint access | Demo: verify App SP `CAN_QUERY` was granted. Prod: verify `app/app.py:_user_client` reads `x-forwarded-access-token` and the target `user_api_scopes` include `serving.serving-endpoints` |
| CLEARS Latency axis fails | Agent Bricks orchestration or Knowledge Assistant source is too broad | Narrow the Knowledge Assistant source, tune Supervisor instructions, or reduce structured-tool fan-out |
| Bootstrap blocks on Lakebase soft-delete | `lakebase_instance` name held by retention | Bump suffix in `databricks.yml` and retry |
| App deploy fails on OBO scopes | Workspace lacks user-token-passthrough feature | Workspace admin enables the feature for prod. Demo should use `app_obo_required=false` unless validating OBO |
