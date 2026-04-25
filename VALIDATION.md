# Validation Guide — Databricks Document Intelligence Agent

Use this guide to prove the reference implementation works in a Databricks workspace.

## Local Static Checks

```bash
python3 -m py_compile \
  agent/_obo.py agent/analyst_agent.py agent/log_and_register.py \
  agent/retrieval.py agent/supervisor.py agent/tools.py \
  app/app.py app/lakebase_client.py \
  evals/clears_eval.py scripts/wait_for_kpis.py samples/synthesize.py

bash -n scripts/bootstrap-dev.sh
pytest agent/tests
databricks bundle validate --strict -t dev
```

Expected prod safety check:

```bash
databricks bundle validate --strict -t prod
```

This should fail unless `service_principal_id` is provided.

## Fresh Dev Bring-Up

```bash
export DOCINTEL_CATALOG=workspace
export DOCINTEL_SCHEMA=docintel_10k_dev
export DOCINTEL_WAREHOUSE_ID=<warehouse-id>

./scripts/bootstrap-dev.sh
```

Expected outcomes:

- Foundation resources deploy first.
- Synthetic PDFs upload to the `raw_filings` volume.
- Pipeline creates Gold rows.
- Agent model registers in Unity Catalog.
- Consumer resources deploy cleanly.
- App config is applied with `bundle run analyst_app`.
- Bootstrap prints either OBO scope verification or an explicit app-level OBO disabled warning.
- Smoke query returns a grounded answer.

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
  --endpoint analyst-agent-dev \
  --dataset evals/dataset.jsonl
```

Expected:

- Correctness, adherence, relevance, execution, safety, and latency thresholds pass.
- P2 and P3 correctness slices are logged.
- No citations reference `garbage_10K_2024.pdf`.

## App Checks

- Open `doc-intel-analyst-dev`.
- Ask: `What was ACME's revenue in fiscal year 2024?`
- Confirm the response has citations and the turn is written to Lakebase.
- Submit thumbs feedback and confirm a feedback row is written.

## OBO Verification

If app-level OBO is enabled:

- Confirm `resources/consumers/analyst.app.yml:user_api_scopes` is uncommented.
- Run `databricks bundle deploy -t dev && databricks bundle run -t dev analyst_app`.
- Confirm bootstrap or CI verifies `serving.serving-endpoints` and `sql` scopes.
- Check audit logs for user-scoped downstream access.

If app-level OBO is not enabled:

- Treat the deployment as reference/dev only.
- Do not claim user-level UC row/column enforcement.
