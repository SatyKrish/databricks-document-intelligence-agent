# Validation Guide — Databricks Document Intelligence Agent

Use this guide to prove the reference implementation works in a Databricks workspace.

## Local Static Checks

```bash
python3 -m py_compile \
  agent/tools.py \
  app/app.py app/lakebase_client.py \
  evals/clears_eval.py scripts/bootstrap_agent_bricks.py \
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
- App config is applied with `bundle run analyst_app`.
- Bootstrap verifies mandatory OBO scopes.
- Smoke query reaches the Agent Bricks supervisor endpoint.

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
  --endpoint analyst-agent-demo \
  --dataset evals/dataset.jsonl
```

Expected:

- Correctness, adherence, relevance, execution, safety, and latency thresholds pass.
- P2 and P3 correctness slices are logged.
- No citations reference `garbage_10K_2024.pdf`.

## App Checks

- Open `doc-intel-analyst-demo`.
- Ask: `What was ACME's revenue in fiscal year 2024?`
- Confirm the response has citations and the turn is written to Lakebase.
- Submit thumbs feedback and confirm a feedback row is written.

## OBO Verification

- Confirm `resources/consumers/analyst.app.yml:user_api_scopes` is present.
- Run `databricks bundle deploy -t demo && databricks bundle run -t demo analyst_app`.
- Confirm bootstrap or CI verifies `serving.serving-endpoints` and `sql` scopes.
- Check audit logs for user-scoped downstream access through Agent Bricks, Knowledge Assistant, and the structured KPI SQL function.
- If the workspace cannot grant user-token passthrough, deployment is invalid and must fail.
