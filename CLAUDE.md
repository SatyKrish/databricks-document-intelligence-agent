# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository status

Active feature: **001-doc-intel-10k** — Databricks Document Intelligence + Agent Bricks 10-K Analyst.
Drives a Bronze→Silver→Gold pipeline (`ai_parse_document` / `ai_classify` / `ai_extract`),
Mosaic AI Vector Search index, agent endpoint behind AI Gateway, Streamlit App on Databricks Apps,
Lakebase state, Lakehouse Monitoring, and an MLflow CLEARS eval gate — all in one DAB.

For an end-to-end overview written for humans, read [`README.md`](./README.md).

## Critical: deploy ordering hazard (READ FIRST before touching deploys)

The bundle has three chicken-egg dependencies that prevent a single `databricks bundle deploy -t dev` from succeeding on a fresh workspace:

1. **Model Serving endpoint** references a registered model version that doesn't exist until `agent/log_and_register.py` runs.
2. **Lakehouse Monitor** (`resources/monitors/kpi_drift.yml`) attaches to `gold_filing_kpis`, which doesn't exist until the pipeline runs once.
3. **Lakebase database_catalog + Databricks App** race the `database_instance` provisioning.

**Canonical fix**: Run `./scripts/bootstrap-dev.sh` for fresh stand-ups; plain `databricks bundle deploy -t dev` for steady-state. The script tolerates expected first-deploy errors, runs the pipeline, registers the agent, and re-deploys to converge.

**Do NOT try to "fix" these by:**
- Adding `depends_on` between heterogeneous DAB resource types — DAB doesn't reliably honor it across instance↔catalog↔app.
- Switching `resources/serving/agent.serving.yml` to UC alias syntax (`@dev`) — DAB rejects it in this workspace; that's why `_promote_serving_endpoint` exists in `agent/log_and_register.py`.
- Splitting monitors into a separate target overlay — adds complexity for a one-time concern.

Full breakdown lives in [`docs/runbook.md`](./docs/runbook.md) §"Known deploy ordering gaps".

## Where things live

```
pipelines/sql/    Lakeflow SDP — Bronze → Silver → Gold (SQL only, principle III)
agent/            Mosaic AI Agent Framework: pyfunc, retrieval, supervisor, UC tools, registration
app/              Streamlit on Databricks Apps + Lakebase psycopg client
evals/            MLflow CLEARS gate (clears_eval.py + dataset.jsonl)
jobs/             Lakeflow Jobs Python tasks (retention, index_refresh)
resources/        DAB resource YAML (one subdir per kind: pipelines/, jobs/, vector_search/, serving/, lakebase/, monitors/, dashboards/, apps/, dabs/)
scripts/          Operational scripts (bootstrap-dev.sh)
samples/          Sample 10-K for smoke tests
specs/001-…       Spec-Kit artifacts (spec, plan, tasks, research, data-model, contracts, quickstart)
docs/runbook.md   Day-2 ops + bring-up workflow
.specify/         Spec-Kit machinery (constitution.md is the source of truth)
```

## Build & deploy

- Validate: `databricks bundle validate -t dev`
- Fresh stand-up: `./scripts/bootstrap-dev.sh` (requires `DOCINTEL_CATALOG`, `DOCINTEL_SCHEMA`, `DOCINTEL_WAREHOUSE_ID`)
- Steady-state deploy: `databricks bundle deploy -t dev`
- Run pipeline: `databricks bundle run -t dev doc_intel_pipeline`
- Run eval: `python evals/clears_eval.py --endpoint analyst-agent-dev --dataset evals/dataset.jsonl`

## Tests & validation

- `pytest agent/tests/` — unit tests for retrieval, agent routing, supervisor
- `databricks bundle validate -t dev` and `-t prod` — schema check both targets before merging
- The CLEARS eval is the deploy gate; principle V says no agent ships without it passing

## Working with this codebase — gotchas Claude has learned

These were discovered the painful way during the 2026-04-25 bring-up. Future sessions: don't re-discover them.

- **SDP streaming chains require explicit `STREAM(...)`**: a temp view that reads from `STREAM(upstream_table)` is itself a streaming view, and downstream references must wrap it in `STREAM(...)` again. Reference: `pipelines/sql/02_silver_parse.sql:23` (`FROM STREAM(silver_with_parsed)`).
- **MLflow + UC requires both inputs AND outputs in signatures**: an inputs-only signature is rejected at registration. For variable-shape fields like `citations` (array of dicts), use `mlflow.types.schema.AnyType()` to avoid serving-time truncation. Reference: `agent/log_and_register.py:_signature`.
- **`lakebase_stopped: true` is rejected on instance creation**: the API doesn't allow creating a database_instance directly into stopped state. Default is `false`; flip to `true` only after the instance exists. Reference: `databricks.yml` variable description.
- **macOS doesn't ship `python`**: scripts must prefer `.venv/bin/python` then fall back to `python3`. Reference: `scripts/bootstrap-dev.sh`.
- **`agent/log_and_register.py` needs `PYTHONPATH`**: it imports the `agent` package; run with `PYTHONPATH=$REPO_ROOT` or use the bootstrap script which exports it.
- **Serving endpoint version drifts from YAML**: `resources/serving/agent.serving.yml` pins `entity_version: "1"` as the bootstrap value. Steady-state CI re-registers new versions and uses `_promote_serving_endpoint` to update the served entity in-place. The YAML and the live endpoint diverge over time — that's intentional, not drift.

## Spec-Kit cycle

Workflow: `/speckit-specify` → `/speckit-clarify` → `/speckit-plan` → `/speckit-tasks`
→ `/speckit-analyze` → `/speckit-implement`. Auto-commits on each phase via
`.specify/extensions.yml` git hooks. The constitution at `.specify/memory/constitution.md`
defines six non-negotiable principles every plan must respect.

<!-- SPECKIT START -->
Active plan: [specs/001-doc-intel-10k/plan.md](./specs/001-doc-intel-10k/plan.md)
Spec: [specs/001-doc-intel-10k/spec.md](./specs/001-doc-intel-10k/spec.md)
Constitution: [.specify/memory/constitution.md](./.specify/memory/constitution.md)
<!-- SPECKIT END -->
