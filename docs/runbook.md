# Operating Runbook — 10-K Analyst

This runbook covers day-2 operations for the deployed dev/prod stacks. For first-time setup follow [`specs/001-doc-intel-10k/quickstart.md`](../specs/001-doc-intel-10k/quickstart.md).

## Add a sample filing

1. `databricks fs cp <path>/<TICKER>_10K_<YEAR>.pdf dbfs:/Volumes/<catalog>/<schema>/raw_filings/`
2. Watch the pipeline: `databricks bundle run -t dev doc_intel_pipeline`
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

## Roll an agent endpoint version

The Model Serving endpoint follows the UC Model Alias `@dev` (or `@prod`), not a pinned version. To roll forward:

```bash
DOCINTEL_CATALOG=<catalog> DOCINTEL_SCHEMA=<schema> python agent/log_and_register.py --target dev
```

This registers a new version and reassigns `@dev`. The serving endpoint will pick the new version on its next traffic refresh (a few minutes). To roll back, use the UC Model Registry UI to re-point the alias to the prior version.

## Inspect CLEARS metrics in MLflow

CI runs `python evals/clears_eval.py --endpoint analyst-agent-dev` after each `dev` deploy. Look for the experiment `/Shared/docintel-clears-<user>`; each run logs:

- Per-axis metrics: `correctness`, `adherence`, `relevance`, `execution`, `safety`, `latency_p95_ms`
- Per-category slices: `p2_correctness`, `p3_correctness`
- Per-question latency: `latency_ms_<id>`

Failures are logged as a JSON list under the run tag `failures`. The script exit-code-fails the deploy if any threshold is missed (FR-010, SC-002, SC-003).

## Common failure modes

| Symptom | Likely cause | Action |
|---|---|---|
| `bundle validate` fails on `ai_parse_document` | Workspace lacks AI Functions GA | Move SQL warehouse to a recent serverless channel |
| Vector Search index sync stuck | Embedding endpoint not provisioned | Provision `databricks-bge-large-en` or override `var.embedding_model_endpoint_name` |
| Agent endpoint 401 | AI Gateway identity passthrough mis-config | Verify `ai_gateway` block in `resources/serving/agent.serving.yml` |
| CLEARS Latency axis fails | Re-rank window too large | Reduce candidate window in `agent/retrieval.py` from 25 to 15 |
| App errors connecting to Lakebase | DSN secret missing | Check `app/app.yaml` env binding and Databricks Apps secret store |

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

(populate after the first successful `dev` deploy)

```
MLflow run ID:   <fill in>
Deployed at:     <date>
P2 correctness:  <value>
P3 correctness:  <value>
Latency p95:     <ms>
```

## Known deploy ordering gaps (discovered in the 2026-04-24 smoke test)

The bundle has three chicken-egg dependencies that a single `bundle deploy` cannot
resolve on a fresh workspace. Each needs a phase-2 step after a prior side effect:

1. **Model Serving endpoint references the agent model alias `@dev`**
   - `resources/serving/agent.serving.yml` sets `entity_version: "@${bundle.target}"`.
   - This fails with `Entity version must be a number` until the model is logged
     and the alias assigned (`agent/log_and_register.py --target dev`).
   - **Fix**: split deploy. (a) Run a one-time bootstrap that calls
     `log_and_register.py` against an empty pipeline run, OR (b) change the
     yml to a literal version after the first registration. The GH Actions
     workflow already orders register before serving — local single-shot deploys
     need the same split.

2. **Lakehouse Monitor references `gold_filing_kpis` which the pipeline must create first**
   - `resources/monitors/kpi_drift.yml` attaches to a table that doesn't exist
     until the pipeline has run at least once.
   - **Fix**: move the monitor into a separate `bundle deploy --include monitors`
     step run after the first pipeline trigger, or comment out the monitor on
     fresh deploys and add it after the first ingest.

3. **Lakebase `database_catalog` and `App` race the `database_instance` provisioning**
   - The catalog and app attach to the instance before the instance has finished
     coming up. Re-running `bundle deploy` immediately after the first attempt
     usually succeeds since the instance is then ready.
   - **Fix**: `bundle deploy -t dev` twice on first stand-up, or add a wait task.

A clean fresh-workspace bring-up sequence:

```bash
# 1. Initial deploy. Expect 3 errors: serving, monitor, app.
DATABRICKS_BUNDLE_ENGINE=direct databricks bundle deploy -t dev

# 2. Apply UC grants.
databricks api post /api/2.1/unity-catalog/permissions/SCHEMA/<catalog>.<schema> \
  --json '{"changes":[{"principal":"<analyst_group>","add":["USE_SCHEMA","SELECT","EXECUTE"]}]}'

# 3. Drop a sample 10-K into the volume; trigger pipeline; wait for gold_filing_kpis.

# 4. Register the agent model and assign @dev alias.
DOCINTEL_CATALOG=<catalog> DOCINTEL_SCHEMA=<schema> python agent/log_and_register.py --target dev

# 5. Re-deploy. Serving + monitor + app should all succeed now.
DATABRICKS_BUNDLE_ENGINE=direct databricks bundle deploy -t dev
```

A future iteration should fold steps 2–5 into the `.github/workflows/deploy.yml`
or a dedicated `scripts/bootstrap-dev.sh` so a single command re-creates the
entire stack.
