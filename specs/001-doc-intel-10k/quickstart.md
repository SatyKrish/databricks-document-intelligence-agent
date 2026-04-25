# Quickstart: Deploy and Test the 10-K Analyst

Goal: from a clean clone, stand up the entire stack on the Databricks `dev` target and verify P1, P2, P3 acceptance scenarios in under 30 minutes.

## Prerequisites

- macOS or Linux, `python` 3.11, `git`, `databricks` CLI ≥ 0.260 (`brew install databricks/tap/databricks`)
- A Databricks workspace with: serverless SQL warehouse (AI Functions GA), Mosaic AI Vector Search and Model Serving entitlements, Lakebase enabled
- An auth profile (`databricks auth login --host <workspace-url>` once); verify with `databricks auth profiles`

## 1. Configure the bundle

```bash
cd databricks
cp databricks.yml.example databricks.yml   # if a template is provided
databricks bundle validate -t dev \
  --var catalog=<your_catalog> \
  --var schema=docintel_10k \
  --var workspace_host=<your_workspace_host>
```

If validate prints no errors, every resource yaml is schema-correct.

## 2. Deploy

```bash
databricks bundle deploy -t dev \
  --var catalog=<your_catalog> \
  --var schema=docintel_10k \
  --var workspace_host=<your_workspace_host>
```

This creates: catalog/schema if absent, the `raw_filings` UC volume, the Lakeflow SDP pipeline, the index-refresh + retention Jobs, the Vector Search endpoint + index, the Lakebase database + 3 tables, the agent serving endpoint behind AI Gateway, the Lakeview dashboard, the Lakehouse Monitor, and the Streamlit App.

## 3. Verify P1 — ingest, parse, extract

Upload a sample SEC 10-K (e.g., `AAPL_10K_2024.pdf` from EDGAR) to the volume:

```bash
databricks fs cp ./samples/AAPL_10K_2024.pdf \
  dbfs:/Volumes/<your_catalog>/docintel_10k/raw_filings/
```

The pipeline trigger fires on file arrival. Wait ~5 minutes, then:

```sql
-- in a SQL warehouse query editor or `databricks sql query --warehouse <id>`
SELECT filename, company_name, fiscal_year, revenue, ebitda,
       size(top_risks) AS num_risks
  FROM <your_catalog>.docintel_10k.gold_filing_kpis
 WHERE filename = 'AAPL_10K_2024.pdf';
```

Expect one row with non-null revenue, ebitda, fiscal_year, company_name, and a non-empty `top_risks` array. Confirms SC-001, SC-008.

## 4. Verify P2 — ask the corpus

Open the deployed App URL printed by `bundle deploy`. Ask:

> What were the top 3 risk factors disclosed by Apple in their FY24 10-K?

Expect: a grounded answer naming ≥ 3 risks, each with a citation chip linking back to `AAPL_10K_2024.pdf` / `Risk`. Submit thumbs-up; refresh the App; the row should be visible in the feedback panel. Confirms SC-002, SC-007.

## 5. Verify P3 — cross-company

Upload two more filings (e.g., `MSFT_10K_2024.pdf`, `GOOG_10K_2024.pdf`), wait for pipeline runs, then ask:

> Compare segment revenue between Apple, Microsoft, and Google in their most recent 10-Ks.

Expect: a markdown table with one row per company, segment-revenue values that match `gold_filing_kpis.segment_revenue`, and citations pointing at each filing. Confirms SC-003.

## 6. Run CLEARS evaluation

```bash
python evals/clears_eval.py \
  --endpoint analyst-agent-dev \
  --dataset evals/dataset.jsonl \
  --catalog <your_catalog> \
  --schema docintel_10k
```

Exit 0 iff every CLEARS axis meets thresholds (C≥0.8, L p95≤8s, E≥0.95, A≥0.9, R≥0.8, S≥0.99). Confirms FR-010 / SC-002 / SC-003.

## 7. Tear down

```bash
databricks bundle destroy -t dev
```

Removes the pipeline, jobs, index, endpoint, app, monitor, dashboard, and Lakebase database. The catalog + volume + Delta tables are preserved unless you also pass `--auto-approve` and explicitly delete them.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `bundle validate` errors on `ai_parse_document` | Workspace lacks AI Functions GA | Ensure SQL warehouse is on a recent serverless channel |
| `Vector Search index sync` stuck pending | Embedding endpoint not entitled | Provision a Mosaic embedding endpoint or set `embedding_model_endpoint_name` in `resources/vector_search/filings_index.yml` |
| Agent endpoint 401 from App | Identity passthrough not configured | Verify AI Gateway config in `resources/serving/agent.serving.yml` |
| CLEARS Latency axis fails | Re-rank window too large | Reduce `top_k` candidates from 25 to 15 in `agent/retrieval.py` |
