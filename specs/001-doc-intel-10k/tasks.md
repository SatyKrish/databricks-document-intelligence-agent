---
description: "Task list for Databricks 10-K Analyst implementation"
---

# Tasks: Databricks 10-K Analyst (Document Intelligence + Agent Bricks)

**Input**: Design documents from `/specs/001-doc-intel-10k/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Generated for the agent layer (pytest unit tests on retrieval and supervisor) and for the eval gate (CLEARS run). Pipeline correctness is exercised end-to-end via `quickstart.md` since SDP doesn't lend itself to local unit testing.

**Organization**: Tasks grouped by user story (US1=P1 Ingest+Parse+Extract, US2=P2 Single-filing Q&A, US3=P3 Cross-company aggregation) so each can be implemented and demoed independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Maps task to user story (US1, US2, US3)
- File paths are repo-relative

## Path Conventions

This is a single-DAB Databricks project. SQL pipeline code at `pipelines/sql/`, Python agent at `agent/`, Streamlit App at `app/`, evals at `evals/`, bundle resources at `resources/`. See plan.md for the full tree.

---

## Phase 1: Setup (Shared Infrastructure)

- [ ] T001 Verify `databricks` CLI ≥ 0.260 is installed and `databricks auth profiles` shows a working profile; if missing, follow the official Databricks CLI installation docs
- [x] T002 Create the bundle skeleton at `databricks.yml` with `bundle.name: doc-intel-10k`, `targets: {demo, prod}`, variables `catalog`, `schema`, `workspace_host`, `service_principal_id` (prod only), `embedding_model_endpoint_name`, `quality_threshold` (default 22), `top_k` (default 5)
- [x] T003 [P] Add `.github/workflows/deploy.yml` running `databricks bundle validate -t demo` on PR and `databricks bundle deploy -t demo` + `python evals/clears_eval.py` on push to `main`
- [x] T004 [P] Create empty `pipelines/sql/`, `agent/`, `app/`, `evals/`, `resources/{pipelines,jobs,vector_search,serving,lakebase,monitors,dashboards,apps}/` directories with `.gitkeep` files
- [x] T005 [P] Add `agent/requirements.txt` (`mlflow>=2.20`, `databricks-agents`, `databricks-vectorsearch`, `databricks-sdk`) and `app/requirements.txt` (`streamlit`, `databricks-sdk`, `psycopg[binary]`)

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ CRITICAL**: All user stories depend on these.

- [x] T006 Define UC catalog/schema/volume in `resources/dabs/catalog.yml` (or inline in `databricks.yml`): `${var.catalog}.${var.schema}` schema + `raw_filings` volume; grant `USE_CATALOG`, `USE_SCHEMA`, `READ_VOLUME` to a configurable analyst group
- [x] T007 [P] Define the Lakebase project + database in `resources/lakebase/state.yml` with three tables (`conversation_history`, `query_logs`, `feedback`) per `data-model.md`; expose connection vars to the Streamlit App
- [x] T008 [P] Add the agent JSON contracts to the bundle as inline strings or copy them to `agent/contracts/` so both the agent and the App reference one source: `agent-request.json`, `agent-response.json`, `feedback-event.json`, `kpi-schema.json`

**Checkpoint**: catalog, schema, volume, Lakebase database exist; bundle validates.

---

## Phase 3: User Story 1 — Ingest + Parse + Extract (Priority: P1) 🎯 MVP

**Goal**: Drop a 10-K PDF into the volume; within 10 minutes, structured KPIs are queryable in `gold_filing_kpis`.

**Independent Test**: `databricks fs cp samples/AAPL_10K_2024.pdf` to the volume → wait → `SELECT * FROM gold_filing_kpis WHERE filename = 'AAPL_10K_2024.pdf'` returns one row with non-null `revenue`, `ebitda`, `top_risks`, `quality_score`.

### Implementation for US1

- [x] T009 [P] [US1] Write `pipelines/sql/01_bronze.sql`: streaming table `bronze_filings` from `read_files()` with `cloudFiles.format=BINARYFILE`, `cloudFiles.schemaLocation`, `cloudFiles.useNotifications=false`; columns per `data-model.md` Bronze section. Filter `WHERE length <= 52428800` (50 MB) and write rejected rows to `bronze_filings_rejected(filename, length, rejected_at, reason)` per FR / spec edge case "PDFs larger than 50 MB"
- [x] T010 [US1] Write `pipelines/sql/02_silver_parse.sql`: streaming table `silver_parsed_filings` using `APPLY CHANGES INTO` keyed on `filename`, computing `ai_parse_document(content)` once into `VARIANT` `parsed`, plus `parse_status`/`parse_error` derived from `try_cast` of the result (depends on T009)
- [x] T011 [US1] Write `pipelines/sql/03_gold_classify_extract.sql`:
  - Streaming table `gold_filing_sections` exploding `parsed:sections[*]`, calling `ai_classify(section_text, ARRAY('MD&A','Risk','Financials','Notes','Other'))` to populate `section_label`, summarising via `ai_query` into the `summary` column
  - Streaming table `gold_filing_kpis` calling `ai_extract` against the concatenated MD&A + Financials text using the JSON schema in `agent/contracts/kpi-schema.json`, then unpacking into typed columns
  - Both tables use `APPLY CHANGES INTO` keyed on appropriate keys (depends on T010)
- [x] T012 [US1] Write `pipelines/sql/04_gold_quality.sql`: materialized view `gold_filing_quality` invoking `ai_query` 5 times per section row to score parse_completeness, layout_fidelity, ocr_confidence, section_recognizability, kpi_extractability (each 0–6); compute `quality_score` and persist `quality_breakdown` STRUCT (depends on T011)
- [x] T013 [US1] Update `gold_filing_sections` (in T011 or a follow-on view) to add `embed_eligible = (quality_score >= ${var.quality_threshold} AND parse_status = 'ok')` by joining with `gold_filing_quality`
- [x] T014 [US1] Define the Lakeflow SDP in `resources/pipelines/doc_intel.pipeline.yml`: serverless, libraries point at `pipelines/sql/*.sql`, target = `${var.catalog}.${var.schema}`, file-arrival event trigger on the `raw_filings` volume, retries=2 (depends on T009-T013)
- [x] T015 [US1] Define the retention Lakeflow Job in `resources/jobs/retention.job.yml`: daily schedule, single Python task that lists the volume via `WorkspaceClient.files`, removes files with `modificationTime < now()-90d`, logs deletions; uses Service Principal in prod only (depends on T006)
- [x] T016 [US1] Add synthetic samples (`samples/{ACME,BETA,GAMMA}_10K_2024.pdf` + `samples/garbage_10K_2024.pdf` for SC-006) reproducible from `samples/synthesize.py`; documented in `samples/README.md`
- [x] T017 [US1] Write a Lakeview `resources/dashboards/usage.lvdash.yml` containing one initial widget over `gold_filing_kpis` (count by company_name, count by fiscal_year); will be extended in US2/US3 (depends on T011)

**Checkpoint**: P1 acceptance scenarios 1–4 pass via the quickstart commands.

---

## Phase 4: User Story 2 — Single-filing Q&A with citations (Priority: P2)

**Goal**: Analyst opens the App, asks a single-filing question, gets a grounded cited answer; thumbs feedback persists.

**Independent Test**: With at least one Apple 10-K in Gold and indexed, ask "What were the top 3 risk factors disclosed by Apple in their FY24 10-K?" → answer names ≥3 risks each with a citation chip → submit thumbs-down with comment → row appears in `lakebase.feedback`.

### Tests for US2 (TDD)

- [x] T018 [P] [US2] Add `agent/tests/test_retrieval.py` covering: hybrid retrieval returns ≤25 candidates, re-rank trims to `top_k`, `embed_eligible=false` rows never returned, `company_filter` and `fiscal_year_filter` are honored
- [x] T019 [P] [US2] Add `agent/tests/test_agent.py` covering: agent returns `grounded=true` only when ≥1 citation present, "no source found" path triggers when retrieval is empty, response validates against `contracts/agent-response.json`

### Implementation for US2

- [x] T020 [P] [US2] Define the Vector Search endpoint in `resources/foundation/filings_index.yml`; the Delta-Sync index over `${var.catalog}.${var.schema}.gold_filing_sections_indexable` is created by `jobs/index_refresh/sync_index.py` because DAB does not manage Vector Search indexes directly (depends on T013)
- [x] T021 [P] [US2] Define the index-refresh Lakeflow Job in `resources/consumers/index_refresh.job.yml` with a table-update trigger on `gold_filing_sections_indexable` and a Python task that creates/syncs `${var.catalog}.${var.schema}.filings_summary_idx` (depends on T020)
- [x] T022 [US2] Implement `agent/retrieval.py`: `hybrid_retrieve(question, top_k=25, filters=None)` calling Vector Search with `query_type='HYBRID'`, then `mosaic_rerank(question, candidates, top_k=5)`; returns list of citation dicts matching `agent-response.json` (depends on T020; tests T018 must fail first)
- [x] T023 [US2] Implement `agent/tools.py`: a UC Function tool wrapping `SELECT * FROM gold_filing_kpis WHERE filename = :filename` for the agent to access structured KPIs deterministically
- [x] T024 [US2] Implement `agent/analyst_agent.py`: a `mlflow.pyfunc` model class implementing the Mosaic AI Agent Framework chat protocol; uses `retrieval.hybrid_retrieve` for grounding, calls a foundation model endpoint to generate the answer, returns the schema in `contracts/agent-response.json` (depends on T022, T023)
- [x] T025 [US2] Implement `agent/log_and_register.py`: `mlflow.pyfunc.log_model(...)`, `mlflow.register_model(...)` to UC at `${var.catalog}.${var.schema}.analyst_agent`; assign UC Model Alias `@demo` (and later `@prod`) to the freshly registered version so Model Serving in T026 follows the alias rather than a frozen version (depends on T024)
- [x] T026 [US2] Define the Model Serving endpoint in `resources/consumers/agent.serving.yml`: CPU instance, served entity = `${var.catalog}.${var.schema}.analyst_agent`, AI Gateway with rate limit + audit enabled (depends on T025)
- [x] T027 [US2] Implement `app/app.py` (Streamlit): chat input, calls the agent endpoint via `databricks.sdk.WorkspaceClient.serving_endpoints.query`, renders answer + citations as chips that show filename + section on hover, thumbs-up/down + comment widget that POSTs to a Lakebase write helper; persists `conversation_id` in session state (depends on T026, T007)
- [x] T028 [US2] Implement `app/lakebase_client.py`: thin wrapper using `psycopg` with the bundle-injected DSN to insert into `conversation_history`, `query_logs`, `feedback`
- [x] T029 [US2] Define the Databricks App in `resources/consumers/analyst.app.yml`: source = `app/`, runtime python, env = Lakebase binding + agent endpoint binding (depends on T027, T028)
- [x] T030 [US2] Author `evals/dataset.jsonl` 20 P2 questions per `data-model.md`'s eval section (each with `expected_filename`, `expected_section`, `expected_answer_keywords`, `min_citations`)
- [x] T031 [US2] Implement `evals/clears_eval.py`: connects to the demo endpoint, runs `mlflow.evaluate()` with `databricks-agents` evaluators on the dataset, asserts thresholds C≥0.8, L p95≤8s, E≥0.95, A≥0.9, R≥0.8, S≥0.99; exits non-zero on failure (depends on T026, T030)
- [x] T032 [US2] Define Lakehouse Monitoring in `resources/consumers/kpi_drift.yml`: `inference` profile on `gold_filing_kpis`, slicing on `company_name`, `fiscal_year`; baselines computed from first 10 filings (depends on T011)
- [x] T033 [US2] Extend `resources/dashboards/usage.lvdash.yml` with widgets over `lakebase.query_logs`: top questions, daily active users, p95 latency, citation count distribution, ungrounded-answer rate (depends on T028, T017)

**Checkpoint**: P2 acceptance scenarios 1–3 pass via App; CLEARS gate passes for the P2 slice of the eval set.

---

## Phase 5: User Story 3 — Cross-company aggregation (Priority: P3)

**Goal**: Analyst asks a multi-company comparison; supervisor returns a markdown table with per-row citations.

**Independent Test**: With three filings (AAPL, MSFT, GOOG) in Gold, ask "Compare segment revenue between Apple, Microsoft, and Google in their most recent 10-Ks" → response is a markdown table with one row per company, segment-revenue numbers match `gold_filing_kpis`, each row has at least one citation.

### Tests for US3 (TDD)

- [x] T034 [P] [US3] Add `agent/tests/test_supervisor.py` covering: supervisor fans out 1 sub-question per detected company, missing companies trigger explicit "not in corpus" handling, the rendered markdown table shape matches expected (header + N rows + citations column), aggregation is deterministic for the same inputs

### Implementation for US3

- [x] T035 [US3] Implement `agent/supervisor.py`: detects company names via a small classifier or LLM call, fans out a per-company query through `analyst_agent`, pulls structured `gold_filing_kpis` rows via `tools.py`, formats a markdown table; returns `agent_path='supervisor'` in the response (depends on T024, T023; tests T034 must fail first)
- [x] T036 [US3] Update `agent/analyst_agent.py` to detect cross-company intent at the routing layer and delegate to `supervisor.handle()`; otherwise stay in single-filing path (depends on T035)
- [x] T037 [US3] Re-run `agent/log_and_register.py` from CI (GH Actions deploy step in T003) to register a new UC model version with the supervisor enabled and re-assign alias `@demo`; the serving endpoint follows the alias so no yml edit is needed
- [x] T038 [US3] Author 10 P3 questions in `evals/dataset.jsonl` (each with `expected_companies` and `expected_table_columns`) (depends on T030)
- [x] T039 [US3] Extend `evals/clears_eval.py` to slice metrics by `category in {P2, P3}` and assert SC-002 ≥0.8 on P2, SC-003 ≥0.7 on P3 (depends on T031, T038)
- [x] T040 [US3] Update `app/app.py` to render markdown tables (Streamlit `st.markdown(..., unsafe_allow_html=False)` already handles this) and surface a "show structured KPIs" expander next to each row (depends on T036)

**Checkpoint**: P3 acceptance scenarios 1–2 pass; CLEARS gate passes for both P2 and P3 slices.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T041 [P] Run `databricks bundle validate -t demo` and resolve any schema warnings
- [ ] T042 [P] Run `databricks bundle validate -t prod` (no deploy) to confirm prod target compiles
- [ ] T043 Walk through `quickstart.md` end-to-end on a clean workspace; capture timing for SC-005
- [x] T044 [P] Add a Lakeview widget on `lakebase.query_logs` summarising "ungrounded answer rate by week" — content-gap signal per Reffy
- [x] T045 [P] Document operating runbook in `docs/runbook.md`: how to add a sample filing, how to debug a low quality_score, how to roll an agent endpoint version, how to inspect CLEARS metrics in MLflow
- [ ] T046 Run `python evals/clears_eval.py` against the demo endpoint and store the MLflow run ID in `docs/runbook.md` as the v1 baseline
- [x] T047 [P] Add an SC-006 verification assertion in `evals/clears_eval.py`: query Vector Search for a known-rejected filename and assert zero hits (verifies "100% rubric exclusion")
- [x] T048 [P] Add an SC-001 timing widget to `resources/dashboards/usage.lvdash.yml` over `gold_filing_kpis` joined to `bronze_filings.ingested_at`: P95 of `extracted_at - ingested_at` per company; alerts if > 10 minutes

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: no deps
- **Phase 2 (Foundational)**: depends on Phase 1; **blocks all user stories**
- **Phase 3 (US1)**: depends on Phase 2
- **Phase 4 (US2)**: depends on Phase 3 (specifically T011, T013) — vector index needs `gold_filing_sections.embed_eligible`
- **Phase 5 (US3)**: depends on Phase 4 (specifically T024) — supervisor wraps `analyst_agent`
- **Phase 6 (Polish)**: depends on all user stories complete

### User Story Dependencies

- **US1**: independent given Phase 2 done
- **US2**: depends on US1's Gold tables to have data to embed
- **US3**: depends on US2's analyst agent to fan out

### Within Each User Story

- Pipeline SQL files: T009 → T010 → T011 → T012 → T013 → T014 (linear within US1)
- Agent code (US2): T020 → T022 → T024 → T025 → T026 → T031 (mostly linear); tests T018/T019 first
- Supervisor (US3): T035 → T036 → T037 → T039 → T040

### Parallel Opportunities

- T003, T004, T005 in Phase 1
- T007, T008 in Phase 2 (after T006)
- T009 (Bronze SQL) parallel with T015 (retention Job) and T016 (sample PDF) and T017 (initial dashboard)
- T020 (VS index yml) and T021 (refresh Job yml) in parallel after T013
- T018 and T019 (agent tests) parallel
- T030 (P2 eval items) and T032 (Lakehouse Monitor) parallel within US2
- T041, T042, T044, T045 in Phase 6

---

## Parallel Example: User Story 2

```bash
# After T013 (gold_filing_sections.embed_eligible), launch in parallel:
Task: "T020 Vector Search index yml"
Task: "T021 Index-refresh Job yml"

# Then write tests in parallel:
Task: "T018 retrieval tests"
Task: "T019 agent contract tests"

# Then implement (sequential within agent/ Python module dependencies):
Task: "T022 retrieval.py"
Task: "T023 tools.py"
Task: "T024 analyst_agent.py" (depends on T022, T023)
```

---

## Implementation Strategy

### MVP First (US1 only)

1. Phase 1 + Phase 2 (one afternoon)
2. Phase 3 / US1 (one day): pipeline runs end-to-end on the sample PDF, Gold KPIs queryable.
3. **STOP, validate via quickstart.md §3**, then demo.

### Incremental delivery

1. MVP (US1) → demo to analyst.
2. Add US2 → demo single-filing Q&A in App.
3. Add US3 → demo cross-company comparison.
4. Phase 6 polish.

### Parallel team strategy

- Engineer A: pipeline SQL (T009–T013) + monitor (T032)
- Engineer B: agent + serving (T022–T026, T031, T035–T037)
- Engineer C: App + Lakebase + dashboard (T027–T029, T033, T040)
- Eng D: bundle scaffolding + CI + retention (T002, T003, T015) and eval dataset (T030, T038)

---

## Notes

- [P] = different files, no upstream-task dependency
- [Story] label maps task to user story for traceability and parallel staffing
- TDD applies to the Python agent code only; SDP pipelines validate via `databricks bundle validate` and end-to-end smoke (no good local unit-test surface for SQL `ai_*` functions)
- Constitution principle V requires the CLEARS gate to pass before US2/US3 deploy is considered complete; T031 + T039 enforce this in CI
- Commit after each task or coherent group; keep PRs small
