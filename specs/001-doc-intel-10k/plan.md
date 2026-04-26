# Implementation Plan: Databricks 10-K Analyst (Document Intelligence + Agent Bricks)

**Branch**: `001-doc-intel-10k` | **Date**: 2026-04-24 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/001-doc-intel-10k/spec.md`

## Summary

Build a Databricks-native, governed pipeline + Agent Bricks system that turns SEC 10-K PDFs into a queryable lakehouse and a cited Q&A experience. SQL Lakeflow Spark Declarative Pipelines parse PDFs once with `ai_parse_document` (VARIANT), classify sections with `ai_classify`, extract structured KPIs with `ai_extract`, and score every section against a 5-dimension quality rubric. High-quality summaries flow into a Mosaic AI Vector Search index. Agent Bricks Knowledge Assistant handles cited document Q&A; Agent Bricks Supervisor Agent coordinates the Knowledge Assistant with a deterministic Unity Catalog KPI function for cross-company comparisons. AI Gateway, Unity Catalog, and mandatory OBO enforce identity and audit. Conversation history and feedback land in Lakebase Postgres. Lakehouse Monitoring tracks extraction drift; an AI/BI dashboard surfaces query-log content gaps. CLEARS evaluation in MLflow gates promotion. The stack is deployed by DAB plus idempotent Agent Bricks bootstrap (`databricks bundle deploy -t demo|prod`, `scripts/bootstrap_agent_bricks.py`).

## Technical Context

**Language/Version**: SQL (Databricks SQL on serverless) for parse/classify/extract pipelines; Python 3.11 for agent + app + eval
**Primary Dependencies**: Lakeflow Spark Declarative Pipelines, Lakeflow Jobs, Mosaic AI Vector Search, Agent Bricks Knowledge Assistant and Supervisor Agent, AI Gateway, Databricks Apps (Streamlit), Lakebase Postgres, Lakehouse Monitoring, Databricks Asset Bundles CLI (`databricks` >= 0.260), MLflow Agent Evaluation
**Storage**: Unity Catalog — `<catalog>.<schema>` with one volume (`raw_filings`) and Delta tables (`bronze_filings`, `silver_parsed_filings`, `gold_filing_sections`, `gold_filing_kpis`); Lakebase Postgres for `conversation_history`, `query_logs`, `feedback`
**Testing**: `databricks bundle validate -t demo` (schema check), pytest for agent unit tests, MLflow `evaluate()` with `databricks-agents` evaluators for CLEARS, manual smoke via the deployed App
**Target Platform**: Databricks workspace with serverless SQL warehouse (AI Functions GA), Mosaic AI Vector Search, Agent Bricks, Databricks Apps user-token passthrough, AI Gateway, Unity Catalog, and Lakebase enabled
**Project Type**: Databricks lakehouse + agent stack delivered as a single DAB
**Performance Goals**: Pipeline E2E ≤ 10 min P95 on a 30 MB PDF (SC-001); agent P95 ≤ 8s single-filing, ≤ 20s cross-company (SC-009); Vector Search refresh ≤ 5 min after Gold update
**Constraints**: SQL only for parse/classify/extract layer; Python only for agent + app; CPU model serving (no GPU); zero hard-coded paths outside the bundle; one-command deploy; CLEARS thresholds C≥0.8, L p95≤8s, E≥0.95, A≥0.9, R≥0.8, S≥0.99 block promotion
**Scale/Scope**: Pilot scale — up to ~500 filings in demo, ~5,000 in prod; ~20 concurrent App users; 30-question eval set

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Evidence |
|---|---|---|
| I. Unity Catalog source of truth (NON-NEGOTIABLE) | ✅ Pass | All tables/volume/index/model/endpoint live under parameterized `<catalog>.<schema>`; defined in `databricks.yml` and `resources/*.yml`. Zero workspace-local resources. |
| II. Parse Once, Extract Many | ✅ Pass | `ai_parse_document` runs once at Silver into VARIANT; classify/extract/prep_search iterate on Gold. |
| III. Declarative over imperative | ✅ Pass | Lakeflow SDP (SQL) for pipelines; Lakeflow Jobs for orchestration; DAB for resources. No production notebooks. |
| IV. Quality before retrieval | ✅ Pass | 5-dim rubric (parse_completeness, layout_fidelity, ocr_confidence, section_recognizability, kpi_extractability); `embed_eligible` boolean filter on the index. Summaries (not raw chunks) embedded. |
| V. Eval-gated agents | ✅ Pass | `evals/clears_eval.py` runs MLflow eval against the demo endpoint; promotion blocked on threshold failure. Lakehouse Monitoring on `gold_filing_kpis`; AI/BI dashboard on `query_logs`. |
| VI. Reproducible deploys | ✅ Pass | `databricks bundle deploy -t demo` recreates the entire stack. Same Python code path runs locally and in Databricks Apps via unified CLI auth. |

**Result**: All gates pass. No deviations to record. Complexity Tracking section intentionally omitted.

## Project Structure

### Documentation (this feature)

```text
specs/001-doc-intel-10k/
├── plan.md              # This file
├── research.md          # Phase 0: technology decisions w/ rationale
├── data-model.md        # Phase 1: entity → table/Postgres mapping
├── quickstart.md        # Phase 1: deploy + test in 30 min
├── contracts/
│   ├── kpi-schema.json          # ai_extract JSON schema
│   ├── agent-request.json       # Agent endpoint request contract
│   ├── agent-response.json      # Agent endpoint response contract w/ citations
│   └── feedback-event.json      # Lakebase feedback row contract
├── checklists/
│   └── requirements.md  # (already exists)
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
databricks.yml                                  # Bundle root, demo/prod targets
resources/
├── foundation/
│   ├── doc_intel.pipeline.yml                  # Lakeflow SDP definition
│   ├── filings_index.yml                       # VS endpoint
│   ├── lakebase_instance.yml                   # Postgres for state
│   └── retention.job.yml                       # 90-day raw PDF cleanup
├── consumers/
│   ├── index_refresh.job.yml                   # Vector Search index create/sync
│   ├── kpi_drift.yml                           # Lakehouse Monitoring
│   ├── usage.dashboard.yml                     # AI/BI Lakeview dashboard
│   ├── lakebase_catalog.yml                    # Lakebase database catalog
│   └── analyst.app.yml                         # Databricks App env binding to generated Agent Bricks endpoint

pipelines/
└── sql/
    ├── 01_bronze.sql                           # Auto Loader cloudFiles BINARYFILE
    ├── 02_silver_parse.sql                     # ai_parse_document → VARIANT
    ├── 03_gold_classify_extract.sql            # ai_classify + ai_extract
    └── 04_gold_quality.sql                     # 5-dim rubric → quality_score

agent/
├── tools.py                                    # deterministic KPI tool glue for Agent Bricks
└── tests/
    └── test_tools.py

app/
├── app.py                                      # Streamlit chat UI
├── app.yaml                                    # Databricks App config
└── requirements.txt

evals/
├── dataset.jsonl                               # 30 questions: 20 P2 + 10 P3
└── clears_eval.py                              # MLflow CLEARS gate

scripts/
├── bootstrap-demo.sh                           # staged deploy orchestration
├── bootstrap_agent_bricks.py                   # Knowledge Assistant + Supervisor bootstrap
└── wait_for_kpis.py

.github/
└── workflows/
    └── deploy.yml                              # validate on PR, deploy -t demo on merge

CLAUDE.md                                       # Runtime guidance for Claude Code
```

**Structure Decision**: Single DAB containing one pipeline, two jobs, one Vector Search endpoint, one Lakebase project, one monitor, one dashboard, one app, and a CI workflow. Agent Bricks resources are SDK-managed by `scripts/bootstrap_agent_bricks.py` until DAB exposes first-class Knowledge Assistant and Supervisor resource types. SQL pipeline code lives at the root under `pipelines/sql/`; deterministic tool glue lives at `agent/`; app code lives at `app/`.

## Phase 0 — Outline & Research

Output: [research.md](./research.md). Decisions captured:

| Topic | Decision | Rationale | Alternatives rejected |
|---|---|---|---|
| Ingestion source | Auto Loader (`cloudFiles`, `BINARYFILE`) over UC volume | Native incremental, schema evolution, file-arrival triggers, no policy work | Lakeflow Connect (deferred, requires SharePoint/Drive credentials in v1); manual SQL `COPY INTO` (no incremental state) |
| Parsing function | `ai_parse_document` GA (SQL) → `VARIANT` | Layout-aware, GA, governed, serverless; matches constitution principle II | Custom OCR + LangChain pipeline (rejected: 5-7× more expensive per blog 1, no governance, doesn't preserve layout) |
| Chunking | `ai_prep_search` (Beta) for paragraph-aware chunks | Embeds Databricks-recommended chunking heuristics; one less hand-rolled component | Hand-rolled splitter (rejected: maintenance burden) |
| Idempotency | `APPLY CHANGES INTO` keyed on `filename` for Silver and Gold | SDP native CDC, deterministic on re-upload, no Python helper | Hand-rolled MERGE (rejected: more code paths); content hash key (deferred — filename is sufficient for v1) |
| Quality rubric | 5 dimensions × 0–6 scale; threshold ≥ 22/30; computed via `ai_query` calls in `04_gold_quality.sql` | Mirrors Reffy's 31-point pattern; SQL-native means no Python helper; explicit dimensions help debug rejections | Single `extraction_confidence` (rejected: no debuggability); 3-dim avg (rejected: too coarse) |
| Vector Search index | Delta-Sync index over `gold_filing_sections` filtered by `embed_eligible`; embed `summary` column | Managed sync, no manual refresh; embeds curated content per principle IV | Direct Vector Index (rejected: no managed sync); embedding raw `parsed.text_full` (rejected: noise) |
| Retrieval strategy | Agent Bricks Knowledge Assistant over the governed document layer / Vector Search source | Demonstrates the Agent Bricks article pattern and removes custom retrieval/rerank serving code | Raw chunk search (rejected: ignores Document Intelligence quality layer) |
| Agent framework | Agent Bricks Knowledge Assistant + Supervisor Agent | First-class governed enterprise agent primitives; aligns with the source articles | Custom `mlflow.pyfunc` analyst agent (rejected: caused deploy-order and serving lifecycle failures); LangGraph standalone (rejected: not the reference pattern) |
| Serving | Agent Bricks endpoint behind AI Gateway with mandatory OBO | Gateway gives audit, rate limits, guardrails, and identity enforcement | Bespoke custom endpoint ownership (rejected: custom lifecycle); service-principal auth for document Q&A (rejected: not production-safe) |
| State store | Lakebase Postgres (managed) | Native to platform, low-latency reads/writes, fits Reffy pattern; integrates with Apps | Delta tables (rejected: write throughput on small turn-level updates); external Postgres (rejected: governance gap) |
| Eval framework | MLflow `evaluate()` with `databricks-agents` evaluators on CLEARS axes | First-class CLEARS support; logged into MLflow runs | LangSmith / Ragas (rejected: external system) |
| Monitoring | Lakehouse Monitoring `inference` profile on `gold_filing_kpis`; Lakeview AI/BI dashboard on `query_logs` | First-class drift detection; usage dashboard surfaces content gaps per Reffy | Custom Spark notebooks (rejected: imperative, principle III) |
| App framework | Streamlit | Fastest in-platform Python UI; Databricks Apps native | React + FastAPI (deferred — Reffy uses this but adds frontend build) |
| CI | GitHub Actions running `databricks bundle validate` (PR) + `bundle deploy -t demo` (merge to main) | Reffy pattern; minimal infra | GitLab/CircleCI (rejected: GitHub is the user's host) |
| Section labels | Canonical set: `MD&A`, `Risk`, `Financials`, `Notes`, `Other` (preserve `original_label`) | Matches FR-003; explicit, testable | Free-form labels (rejected: untestable) |
| Retention | 90-day Lakeflow Job that lists volume, filters `ingested_at < now()-90d`, removes the file | Doesn't depend on workspace lifecycle policies; auditable | UC volume lifecycle rule (rejected: requires admin policy work that can't be assumed) |

No `NEEDS CLARIFICATION` items remain.

## Phase 1 — Design & Contracts

Output: `data-model.md`, `contracts/`, `quickstart.md`, plus the agent context update in `CLAUDE.md`.

### Data Model summary (full mapping in `data-model.md`)

| Spec entity | Physical artifact | Layer |
|---|---|---|
| Filing | `bronze_filings` row + `silver_parsed_filings` row + `gold_filing_kpis` row | Bronze→Gold |
| Section | `gold_filing_sections` row | Gold |
| KPI Record | `gold_filing_kpis` row (JSON-typed `ai_extract` output unpacked into columns) | Gold |
| Citation | Returned in agent response payload (see `contracts/agent-response.json`) | Runtime |
| Conversation | `lakebase.conversation_history` + `lakebase.query_logs` rows | Lakebase |
| Eval Item | `evals/dataset.jsonl` row | Repo |

### Contracts

- `contracts/kpi-schema.json` — JSON schema passed to `ai_extract` (revenue, ebitda, segment_revenue, top_risks, fiscal_year, company_name, extraction_confidence)
- `contracts/agent-request.json` — normalized app request metadata around an Agent Bricks user message
- `contracts/agent-response.json` — `{answer: string, citations: [{filename, section_label, score, char_offset?}], latency_ms: int, retrieved_count: int}`
- `contracts/feedback-event.json` — `{conversation_id, turn_id, user_id, rating: "up"|"down", comment?: string, ts}`

### Quickstart

`quickstart.md` covers: install/auth `databricks` CLI → set bundle vars → `bundle validate -t demo` → `bundle deploy -t demo` → upload sample 10-K → query Gold → open App and ask the example question → run `evals/clears_eval.py`.

### Agent context update

`CLAUDE.md` gets a `<!-- SPECKIT START -->...<!-- SPECKIT END -->` block pointing at `specs/001-doc-intel-10k/plan.md` so future Claude Code sessions in this repo can find the active plan.

## Constitution Check (post-design re-evaluation)

All six principles still pass. Design choices reinforce them:

- **III. Declarative**: Quality rubric moved into SQL pipeline (`04_gold_quality.sql`) instead of a Python helper.
- **IV. Quality**: `embed_eligible` boolean is computed in Gold and is the WHERE clause of the Vector Search Delta-Sync index.
- **V. Eval-gated**: GitHub Actions deploy step calls `evals/clears_eval.py`; non-zero exit blocks the deploy.
- **VI. Reproducible**: Retention is a bundle-managed Lakeflow Job, not an out-of-band workspace policy.

No Complexity Tracking entries needed.
