# Databricks 10-K Analyst

A **Databricks-native document intelligence + agent** stack that turns SEC 10-K annual reports into a queryable lakehouse and a cited Q&A experience — built end-to-end with **Spec-Kit** for spec-driven development and **Claude Code** with the Databricks skill suite for AI-assisted implementation.

> **Status**: Pre-1.0 learning artifact. Open-sourced as a reference implementation for teams adopting Databricks Document Intelligence, Agent Bricks, Spec-Kit, and AI-driven development workflows.

```
   SEC 10-K PDF                       Analyst's question
   (e.g., ACME_10K_2024.pdf)          "What were ACME's top 3 risks in FY24?"
        │                                          │
        ▼                                          ▼
   ┌─────────────────────┐              ┌──────────────────────┐
   │  Pipeline (offline) │ ───────────▶ │  Agent (online)      │
   │  Parse → KPIs       │   indexed    │  Retrieve → Answer   │
   │  Quality scoring    │   knowledge  │  with citations      │
   └─────────────────────┘              └──────────────────────┘
                                                  │
                                                  ▼
                                          "Apple cited supply-chain
                                           risk [1], China exposure
                                           [2], regulation [3]…"
```

---

## What this is

A research analyst's assistant for SEC 10-K annual reports. Drop a PDF into a governed Unity Catalog volume; within ~10 minutes the system parses it (preserving layout), classifies its sections, extracts a structured KPI record (revenue, EBITDA, top risks, segment revenue), scores its quality on a five-dimension rubric, and indexes high-quality summaries for retrieval.

Once indexed, an analyst opens a Streamlit app on Databricks Apps and asks questions in plain English. The agent retrieves the most relevant filing sections, generates a grounded answer with inline citations, and persists the conversation + thumbs feedback to a Lakebase Postgres database. Cross-company comparisons ("compare segment revenue across ACME, BETA, GAMMA") are handled by a supervisor agent that fans out per-company queries and renders a markdown table. The repo ships with synthetic 10-Ks (`samples/{ACME,BETA,GAMMA}_10K_2024.pdf` + a low-quality `garbage_10K_2024.pdf` for SC-006 testing) so the corpus is fully reproducible.

The whole stack — pipeline, vector index, agent, app, monitoring, dashboard, evaluation gate — is one **Databricks Asset Bundle (DAB)**. After a one-time bootstrap, `databricks bundle deploy -t dev` recreates everything.

---

## Architecture at a glance

### Two halves: an offline pipeline, and an online agent

```
   ╔═══════════════════════════════════════════════════════════════════╗
   ║                  pipelines/sql/  (one SQL file per tier)          ║
   ╚═══════════════════════════════════════════════════════════════════╝

  raw_filings/       ┌─────────────────┐   ┌─────────────────┐   ┌──────────────────┐
  ACME_10K.pdf  ──▶  │  bronze_filings │──▶│ silver_parsed_  │──▶│ gold_filing_     │
  BETA_10K.pdf       │  (raw bytes,    │   │ filings (parsed │   │ sections (one    │
  GAMMA_10K.pdf      │   filename,     │   │ VARIANT —       │   │  row per parsed  │
                     │   ingested_at)  │   │ ai_parse_       │   │  $.sections[*];  │
                     │                 │   │ document)       │   │  fallback to     │
                     │  >50MB rejects: │   │                 │   │  full_document   │
                     │  bronze_filings │   │ Status: ok /    │   │  if absent)      │
                     │  _rejected      │   │ partial / error │   │                  │
                     └─────────────────┘   └─────────────────┘   │ gold_filing_kpis │
                          01_bronze.sql       02_silver_parse    │ (typed columns:  │
                                                .sql             │  segment_revenue │
                                                                 │  ARRAY<STRUCT…>, │
                                                                 │  top_risks       │
                                                                 │  ARRAY<STRING>)  │
                                                                 └──────────────────┘
                                                                  03_gold_classify
                                                                  _extract.sql
                                                                          │
                                                                          ▼
                                                                 ┌──────────────────┐
                                                                 │ gold_filing_     │
                                                                 │ quality          │
                                                                 │ (5-dim rubric:   │
                                                                 │  parse, layout,  │
                                                                 │  ocr, sections,  │
                                                                 │  kpi → 0-30)     │
                                                                 └──────────────────┘
                                                                  04_gold_quality.sql
```

**Key idea — "parse once, extract many":** PDFs are expensive to parse. Silver runs `ai_parse_document` exactly once per file and stores the structured result as a `VARIANT`. Everything downstream — classification, KPI extraction, summarization, quality scoring — reads the parsed output, never the raw bytes. This is a non-negotiable constitution principle.

**Triggering**: prod runs the pipeline in `continuous: true` mode so Auto Loader (`read_files`) reacts to new PDFs in the volume automatically. Dev overrides to `continuous: false` to avoid a 24/7 cluster during smoke iterations. See `resources/pipelines/doc_intel.pipeline.yml` and the dev override block in `databricks.yml`.

### Vector Search bridges data and agent

```
   gold_filing_sections           ┌─────────────────────────┐
   (governed Delta table)  ─────▶ │  Mosaic AI Vector       │
                                  │  Search Index           │
   Filter: embed_eligible=true    │  (Delta-Sync — auto-    │
   Embed column: "summary"        │   refreshes when Gold    │
                                  │   updates)              │
                                  └─────────────────────────┘

   Why "summary" not the raw text?
   ─────────────────────────────
   Embedding a 50-page 10-K verbatim is noisy. We embed an LLM-written
   summary instead — tighter, more searchable. Constitution principle IV:
   "Quality before retrieval."
```

### Agent has two paths, one endpoint

```
   User question
        │
        ▼
   ┌────────────────────────────────────────────┐
   │  AnalystAgent.predict()                    │
   │  ─────────────────────                      │
   │   contains "compare" / "vs" /              │
   │   "between" + ≥2 company names?            │
   └────────────┬─────────────────┬─────────────┘
                │ no              │ yes
                ▼                 ▼
   ┌──────────────────────┐  ┌──────────────────────┐
   │ Single-filing path   │  │ Supervisor path      │
   │                      │  │                      │
   │ 1. Hybrid search     │  │ For each company:    │
   │    (keyword + vec)   │  │   ▸ run analyst path │
   │ 2. Re-rank → top 5   │  │   ▸ pull KPIs from   │
   │ 3. LLM generates     │  │     gold_filing_kpis │
   │    answer w/ [1] [2] │  │ Format markdown      │
   │    citations         │  │ table with cites.    │
   └──────────────────────┘  └──────────────────────┘
                │                 │
                └────────┬────────┘
                         ▼
              ┌──────────────────────┐
              │  Response JSON:      │
              │   answer             │
              │   citations[]        │
              │   grounded: bool     │
              │   latency_ms         │
              └──────────────────────┘
```

The agent is an `mlflow.pyfunc` model registered in Unity Catalog and served behind an **AI Gateway** (rate limiting per-user, usage tracking, inference-table audit). Identity passthrough is implemented at the *App layer* — the Streamlit app extracts the user's `x-forwarded-access-token` header and constructs a user-scoped `WorkspaceClient` so any UC SQL the agent runs is governed under the user's identity, not the App SP. See `app/README.md` for the OBO flow.

### Runtime stack

```
   ┌──────────────────────────────────────────────────────────────────┐
   │                                                                  │
   │     Databricks App (Streamlit)  ←  user interacts here          │
   │     app/app.py                                                   │
   │                                                                  │
   │     ┌────────────────┐   ┌──────────────────┐                   │
   │     │ Chat input box │   │ Citation chips   │                    │
   │     │ Thumbs up/down │   │ Markdown tables  │                    │
   │     └────────┬───────┘   └─────┬────────────┘                    │
   │              │                 │                                 │
   └──────────────│─────────────────│─────────────────────────────────┘
                  │                 │
                  │ query           │ feedback writes
                  ▼                 ▼
   ┌────────────────────────┐  ┌────────────────────────┐
   │ Model Serving endpoint │  │  Lakebase Postgres     │
   │ "analyst-agent-dev"    │  │  ─────────────────      │
   │  (CPU, scales to 0)    │  │  conversation_history   │
   │                        │  │  query_logs             │
   │  + AI Gateway:         │  │  feedback               │
   │    rate limit          │  │                        │
   │      (per-user key)    │  │  (Postgres for tiny    │
   │    inference-table     │  │   per-turn writes —    │
   │      audit             │  │   Delta isn't great    │
   │    usage tracking      │  │   at row-by-row)       │
   └────────────────────────┘  └────────────────────────┘

   OBO (user identity end-to-end):
   ──────────────────────────────
   App reads `x-forwarded-access-token` from the request, builds
   `WorkspaceClient(token=...)`, calls the serving endpoint with the
   user's identity. Agent code's downstream UC SQL runs as the user.
   AI Gateway logs per-user usage; UC ACLs enforce row/column rules.
```

**Why Postgres for state?** Delta tables are great for analytics but bad at "insert one tiny row per chat turn at high frequency." Lakebase is Databricks's managed Postgres — same governance, right tool for the job.

---

## How it's built — three pillars

This repo is a worked example of combining three things that, together, change how you ship Databricks projects.

### Pillar 1 — Spec-Kit (spec-driven development)

[Spec-Kit](https://github.com/github/spec-kit) is a workflow that forces you to write — and *clarify* — a specification before writing code. Each phase is a slash-command in Claude Code that produces a checked-in artifact:

```
   /speckit-specify   →  specs/<NNN>/spec.md         What & why (no how)
        │
        ▼
   /speckit-clarify   →  appended Q&A in spec.md     Resolve ambiguity
        │
        ▼
   /speckit-plan      →  specs/<NNN>/plan.md         Tech stack + structure
        │              + research.md, data-model.md,
        │                contracts/, quickstart.md
        ▼
   /speckit-tasks     →  specs/<NNN>/tasks.md        Dependency-ordered tasks
        │
        ▼
   /speckit-analyze   →  cross-artifact consistency check
        │
        ▼
   /speckit-implement →  the actual code
```

`.specify/extensions.yml` auto-commits at each phase boundary so the trail is clean. `.specify/memory/constitution.md` defines six **non-negotiable principles** every plan must respect:

| # | Principle | What it means |
|---|---|---|
| I | **Unity Catalog source of truth** | Every table, volume, model, index, endpoint lives under `<catalog>.<schema>` — no DBFS, no workspace-local resources |
| II | **Parse once, extract many** | `ai_parse_document` runs once at Silver → VARIANT; everything downstream reads the parsed output |
| III | **Declarative over imperative** | SDP SQL pipelines, Lakeflow Jobs, DAB resources — no production notebooks |
| IV | **Quality before retrieval** | 5-dim rubric scores every section; only ≥22/30 reach the index. Embed `summary`, not raw text |
| V | **Eval-gated agents** | MLflow CLEARS scores must clear thresholds before any deploy is considered complete |
| VI | **Reproducible deploys** | `databricks bundle deploy -t <env>` recreates the entire stack; `dev` and `prod` parity enforced |

When you read `specs/001-doc-intel-10k/plan.md` you'll see a "Constitution Check" gate that maps each design decision back to the principle it satisfies. When you read `specs/001-doc-intel-10k/tasks.md` you'll see how each task derives from the plan, and how user-stories (P1, P2, P3) are independently demoable.

### Pillar 2 — Databricks Asset Bundles + the Claude Code skill suite

[**Databricks Asset Bundles**](https://docs.databricks.com/aws/en/dev-tools/bundles/) (DABs) describe the entire workspace state as YAML. One root `databricks.yml` declares variables and targets (`dev`, `prod`); `resources/**/*.yml` declares each resource (pipeline, jobs, vector index, serving endpoint, app, monitor, dashboard, Lakebase). `databricks bundle deploy -t dev` reconciles workspace state to YAML.

This repo was built with seven Databricks-specific Claude Code skills, each maintained with current platform guidance:

| Skill | What it provides |
|---|---|
| **databricks-core** | Auth, profiles, data exploration, bundle basics |
| **databricks-dabs** | DAB structure, validation, deploy workflow, target separation |
| **databricks-pipelines** | Lakeflow Spark Declarative Pipelines (`ai_parse_document`, `ai_classify`, `ai_extract`, `APPLY CHANGES INTO`) |
| **databricks-jobs** | Lakeflow Jobs with retries, schedules, table-update / file-arrival triggers |
| **databricks-apps** | Databricks Apps (Streamlit), App resource bindings (Lakebase, secrets, serving endpoints) |
| **databricks-lakebase** | Lakebase Postgres instances, branches, computes, endpoint provisioning |
| **databricks-model-serving** | Model Serving endpoints, AI Gateway, served entities, scaling config |

Skills are loaded by Claude Code on demand. When you ask Claude to "wire up Vector Search," it reads the `databricks-pipelines` and `databricks-model-serving` skills *before* writing YAML, so the output reflects current Databricks API shapes — not stale training data.

### Pillar 3 — Claude Code as the implementation surface

Spec-Kit produces the specs. The Databricks skills provide platform expertise. **Claude Code orchestrates both**: every phase artifact and every code file in this repo was authored by prompting Claude Code with the spec/plan/tasks as context.

The workflow looks like:

1. `/speckit-specify` → Claude writes spec.md from a natural-language description, you iterate via `/speckit-clarify` until ambiguity is resolved.
2. `/speckit-plan` → Claude consults the constitution + Databricks skills, drafts plan.md with research decisions and architecture.
3. `/speckit-tasks` → Claude generates a dependency-ordered task list grouped by user story (P1, P2, P3).
4. `/speckit-implement` → Claude writes the actual SQL/Python/YAML, one task at a time, committing per task.
5. Operational loops: when the deploy hits unexpected issues (it always does), Claude reads the runbook, fixes the issue, updates the runbook, commits.

The "AI-driven" part isn't "the AI did it for you" — it's "the AI carries the boring parts (boilerplate YAML, retry-loop scripts, dependency analysis) so you focus on the actually-hard parts (what the spec should say, what the constitution should require)."

---

## The deploy ordering problem (and why it's interesting)

DABs deploy *everything in one shot*. But our resources have a chicken-and-egg problem on a fresh workspace:

```
        ┌────────────────────────────────────────────────┐
        │   What "bundle deploy" tries to create:        │
        │                                                │
        │   ▸ Pipeline   ────┐                           │
        │   ▸ Tables     ────┼──── all need each other  │
        │   ▸ Vector idx  ───┤                           │
        │   ▸ Model       ───┤    Monitor wants the      │
        │   ▸ Endpoint   ────┤    KPI table to exist     │
        │   ▸ App         ───┤    BEFORE it can attach   │
        │   ▸ Monitor    ────┘                           │
        │   ▸ Lakebase   ────                            │
        └────────────────────────────────────────────────┘

   Endpoint needs a registered model version.
        Model version needs the model logged.
              Model logging needs the agent code.
                    Monitor needs the table populated.
                          Table needs the pipeline to run.

   ▶ Single `bundle deploy` → 4+ errors on a fresh workspace.
```

The fix is `scripts/bootstrap-dev.sh`, which sequences the bring-up:

```
   Step 1 ▸ bundle deploy     (some errors expected — skipped, not fatal)
   Step 2 ▸ upload sample PDF, trigger pipeline
   Step 3 ▸ wait for gold_filing_kpis to have ≥1 row
   Step 4 ▸ register agent model v1, repoint serving endpoint
   Step 5 ▸ bundle deploy AGAIN → everything resolves
```

After that first run, steady-state CI is just `bundle deploy` because all resources exist. The full breakdown — including the failure modes we hit and tolerated — lives in [`docs/runbook.md`](./docs/runbook.md).

---

## CLEARS quality gate

Before any deploy reaches production, an evaluation must pass. This is constitution principle V — eval-gated agents.

```
   evals/dataset.jsonl  (30 questions: 20 single-filing P2 + 10 cross-company P3)
        │
        ▼
   evals/clears_eval.py  ──▶  hits the dev endpoint, scores 6 axes:

      ┌─────────────────────────────────────────────────────┐
      │  C - Correctness   ≥ 0.80    (factual accuracy)     │
      │  L - Latency p95   ≤ 8000 ms (responsiveness)       │
      │  E - Execution     ≥ 0.95    (no crashes)           │
      │  A - Adherence     ≥ 0.90    (cites sources)        │
      │  R - Relevance     ≥ 0.80    (retrieved good docs)  │
      │  S - Safety        ≥ 0.99    (no harmful output)    │
      └─────────────────────────────────────────────────────┘

   Any axis fails ▶ exit 1 ▶ deploy blocked.
```

The bar is hard-coded; changing it requires editing `.specify/memory/constitution.md`, which is its own small ceremony (PR + version bump + Sync Impact Report).

---

## Quickstart

Prereqs: a Databricks workspace with serverless SQL warehouses, Mosaic AI entitlements, and a configured CLI profile.

```bash
# 1. Validate the bundle compiles
databricks bundle validate -t dev

# 2. First-time bring-up (handles the chicken-egg ordering)
DOCINTEL_CATALOG=workspace \
DOCINTEL_SCHEMA=docintel_10k_dev \
DOCINTEL_WAREHOUSE_ID=<your-warehouse-id> \
./scripts/bootstrap-dev.sh

# 3. Run the CLEARS eval gate
python evals/clears_eval.py --endpoint analyst-agent-dev --dataset evals/dataset.jsonl

# 4. Steady-state deploys (after first bring-up)
databricks bundle deploy -t dev

# 5. Open the App
#    (in workspace UI: Apps → "doc-intel-analyst-dev")
```

For a guided tour from clean workspace to first cited answer, see [`specs/001-doc-intel-10k/quickstart.md`](./specs/001-doc-intel-10k/quickstart.md).

---

## Repo layout

```
databricks/
├── databricks.yml                 # Bundle root — variables + dev/prod targets
├── README.md                      # This file
├── CLAUDE.md                      # Runtime guidance for Claude Code sessions
│
├── pipelines/sql/                 # Lakeflow SDP — Bronze → Silver → Gold (SQL)
│   ├── 01_bronze.sql              # Auto Loader BINARYFILE ingest + size filter
│   ├── 02_silver_parse.sql        # ai_parse_document → VARIANT
│   ├── 03_gold_classify_extract.sql  # ai_classify + ai_extract → KPIs
│   └── 04_gold_quality.sql        # 5-dim rubric → embed_eligible filter
│
├── agent/                         # Mosaic AI Agent Framework
│   ├── analyst_agent.py           # mlflow.pyfunc model + routing
│   ├── retrieval.py               # Hybrid search + re-rank
│   ├── supervisor.py              # Cross-company fan-out
│   ├── tools.py                   # UC Function tool over gold_filing_kpis
│   ├── log_and_register.py        # Register model + repoint serving endpoint
│   └── tests/                     # pytest unit tests
│
├── app/                           # Streamlit App on Databricks Apps
│   ├── app.py                     # Chat UI + citations + thumbs feedback
│   ├── lakebase_client.py         # psycopg writes to query_logs / feedback
│   └── app.yaml                   # App runtime config
│
├── evals/                         # MLflow CLEARS eval gate
│   ├── dataset.jsonl              # 30 hand-authored questions
│   └── clears_eval.py             # 6-axis scoring + threshold enforcement
│
├── jobs/                          # Lakeflow Jobs Python tasks
│   ├── retention/prune_volume.py  # 90-day raw PDF cleanup
│   └── index_refresh/sync_index.py  # Vector Search SYNC INDEX
│
├── resources/                     # DAB resource definitions (one yml per kind)
│   ├── pipelines/                 # Lakeflow SDP definition
│   ├── jobs/                      # Retention + index-refresh
│   ├── vector_search/             # VS endpoint + Delta-Sync index
│   ├── serving/                   # Model Serving + AI Gateway
│   ├── lakebase/                  # Postgres instance + catalog
│   ├── monitors/                  # Lakehouse Monitoring on KPI table
│   ├── dashboards/                # AI/BI Lakeview usage dashboard
│   ├── apps/                      # Databricks App resource
│   └── dabs/                      # Catalog + schema + volume
│
├── scripts/                       # Operational scripts
│   └── bootstrap-dev.sh           # Fresh-workspace bring-up
│
├── samples/                       # Sample 10-K for smoke tests
│   └── ACME_10K_2024.pdf
│
├── specs/                         # Spec-Kit artifacts
│   └── 001-doc-intel-10k/
│       ├── spec.md                # What & why
│       ├── plan.md                # Tech stack + Constitution Check
│       ├── tasks.md               # Dependency-ordered implementation tasks
│       ├── research.md            # Decision log
│       ├── data-model.md          # Entity → table mapping
│       ├── quickstart.md          # 30-min deploy walkthrough
│       └── contracts/             # JSON schemas for KPIs + agent I/O
│
├── docs/
│   └── runbook.md                 # Day-2 ops + bring-up workflow
│
├── .specify/                      # Spec-Kit machinery (constitution, hooks)
│   ├── memory/constitution.md     # Six non-negotiable principles
│   └── extensions.yml             # Auto-commit hooks per phase
│
├── .claude/
│   └── skills/                    # Databricks + Spec-Kit skills (loaded on demand)
│
└── .github/workflows/
    └── deploy.yml                 # PR validate; main → deploy + CLEARS gate
```

---

## What you can learn from this repo

- **How to wire `ai_parse_document` into Lakeflow SDP** — pattern for streaming-tables + `STREAM(...)` views + `APPLY CHANGES INTO` keyed on filename.
- **How to score document quality before retrieval** — five 0–6 dimensions in SQL, threshold filter on the index source.
- **How to log a Mosaic AI agent to UC** — `mlflow.pyfunc` with both inputs *and* outputs in the signature (UC requirement), `AnyType` for variable-shape fields.
- **How to ground an agent with citations** — hybrid Vector Search → re-rank → top-k → LLM with explicit "cite sources [1] [2]" prompt.
- **How to handle DAB deploy ordering** — chicken-egg dependencies between heterogeneous resources, solved with a 5-step bootstrap rather than `depends_on` (which DAB doesn't reliably honor across resource types).
- **How to gate deploys on MLflow eval** — CLEARS axes + per-category slices, exit-code gate in CI.
- **How Spec-Kit + Claude Code + Databricks skills compose** — every artifact in `specs/` and `pipelines/` and `agent/` was generated through that loop.

---

## Status & limitations

This is a **pilot-scale** reference implementation, not a production-ready product:

| Limit | Value | Source |
|---|---|---|
| Filings in dev | ~500 | spec.md scale |
| Filings in prod | ~5,000 | spec.md scale |
| Concurrent app users | ~20 | spec.md scale |
| PDF size cap | 50 MB | FR / `bronze_filings_rejected` |
| Raw retention | 90 days | spec clarification |
| Compute | CPU only | constitution add'l constraints |
| Languages | English filings | implicit (foundation model) |
| Eval set size | 30 questions | spec clarification |

Latency SLOs: P95 ≤ 8s for single-filing, ≤ 20s for cross-company. End-to-end pipeline ≤ 10 min P95 on a 30 MB PDF.

---

## License & attribution

This repository is intended to be **open-sourced for learning purposes**. A `LICENSE` file has not been added yet — choose one (MIT or Apache-2.0 are common picks for learning artifacts) before publishing publicly.

Built with:

- [**Spec-Kit**](https://github.com/github/spec-kit) — spec-driven development workflow for AI coding agents.
- [**Claude Code**](https://claude.com/claude-code) — Anthropic's CLI for AI-assisted development, with the Databricks skill suite.
- [**Databricks Lakehouse + Mosaic AI**](https://www.databricks.com/) — Unity Catalog, Lakeflow Spark Declarative Pipelines, Mosaic AI Vector Search, Agent Framework, Model Serving, AI Gateway, Databricks Apps, Lakebase, Lakehouse Monitoring.

The 10-K analyst pattern is inspired by Databricks's own Reffy reference architecture for governed agent applications.
