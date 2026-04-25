# Databricks Document Intelligence Agent — Reference Implementation

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Databricks CLI ≥0.298](https://img.shields.io/badge/Databricks_CLI-%E2%89%A50.298-orange)](https://docs.databricks.com/aws/en/dev-tools/cli/install)
[![Status: reference](https://img.shields.io/badge/status-reference%20implementation-informational)](./PRODUCTION_READINESS.md)
[![Built with Spec-Kit](https://img.shields.io/badge/built%20with-Spec--Kit-purple)](https://github.com/github/spec-kit)
[![Built with Claude Code](https://img.shields.io/badge/built%20with-Claude%20Code-D97757)](https://claude.com/claude-code)

A **Databricks-native document intelligence + agent** stack: parse PDFs once with `ai_parse_document`, classify and extract structured KPIs with `ai_classify` / `ai_extract`, score quality on a 5-dimension rubric, index high-quality summaries into Mosaic AI Vector Search, and serve a cited-answer agent through a Streamlit app on Databricks Apps. **Demonstrated on synthetic SEC 10-K filings**, but the architecture works for any structured document corpus (contracts, invoices, research reports, regulatory filings).

> [!IMPORTANT]
> Open-source **reference implementation**. The repo demonstrates production-grade Databricks patterns end-to-end, but it is not a turnkey production deployment. Read [`PRODUCTION_READINESS.md`](./PRODUCTION_READINESS.md), [`SECURITY.md`](./SECURITY.md), and [`VALIDATION.md`](./VALIDATION.md) before pointing real users at it.

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
                                          "ACME cited supply-chain
                                           risk [1], AI competition
                                           [2], regulation [3]…"
```

---

## Table of contents

- [Why this exists](#why-this-exists)
- [Features](#features)
- [Readiness levels](#readiness-levels)
- [Prerequisites](#prerequisites)
  - [Software](#software)
  - [Databricks workspace](#databricks-workspace)
  - [Free trial signup](#free-trial-signup)
- [Getting started](#getting-started)
- [Architecture](#architecture)
- [How it's built — three pillars](#how-its-built--three-pillars)
- [Deploy ordering: foundation → consumers](#deploy-ordering-foundation--consumers)
- [CLEARS quality gate](#clears-quality-gate)
- [Configuration](#configuration)
- [Testing & validation](#testing--validation)
- [Deployment](#deployment)
- [Repo layout](#repo-layout)
- [What you can learn from this repo](#what-you-can-learn-from-this-repo)
- [Limitations](#limitations)
- [Contributing](#contributing)
- [Security](#security)
- [License](#license)
- [Acknowledgments](#acknowledgments)

---

## Why this exists

Databricks shipped a lot of new generative-AI surface area in 2025–2026: `ai_parse_document`, Mosaic AI Vector Search, the Agent Framework, AI Gateway, Lakebase, Databricks Apps. Tutorials show each piece in isolation; nobody shows them wired together with **eval gates, governance, and reproducible deploys** the way you'd actually ship to analysts.

This repo is that worked example. Drop a PDF into a governed UC volume; ten minutes later, an analyst can ask cited questions in plain English with end-to-end audit. The whole stack is described declaratively as one **Databricks Asset Bundle (DAB)** plus a small bootstrap script. DAB manages catalog/schema/volume, pipeline, jobs, the Vector Search **endpoint**, the Lakebase instance, the serving endpoint, the monitor, the app, and the dashboard; the Vector Search **index** itself is created and synced by `jobs/index_refresh/sync_index.py` (DAB doesn't yet manage indexes as a resource type), and the agent model version is registered by `agent/log_and_register.py`. The bootstrap script orchestrates them in the right order.

It also demonstrates a development workflow: **Spec-Kit** for spec-driven design, **Claude Code** with Databricks skill bundles for AI-assisted implementation, six **non-negotiable constitution principles** that gate every plan. See [How it's built](#how-its-built--three-pillars).

## Features

- **End-to-end document intelligence pipeline** — Auto Loader ingest → `ai_parse_document` → section explosion → `ai_classify` + `ai_extract` → 5-dim quality rubric → Vector Search Delta-Sync index (the endpoint is DAB-managed; the index is created/synced by `jobs/index_refresh/sync_index.py`). SQL-only pipeline (Lakeflow Spark Declarative Pipelines).
- **Cited-answer agent** — Mosaic AI Agent Framework (MLflow `pyfunc`), hybrid retrieval + Mosaic re-ranker, single-filing and cross-company supervisor paths. Logged with auth_policy for end-to-end OBO when the workspace supports it.
- **Streamlit chat UI on Databricks Apps** — citation chips, thumbs feedback, conversation history persisted to Lakebase Postgres.
- **Eval-gated promotion** — `mlflow.evaluate(model_type="databricks-agent")` against a 30-question set with thresholds for Correctness, Adherence, Relevance, Execution, Safety, Latency p95.
- **Reproducible synthetic corpus** — `samples/synthesize.py` generates ACME / BETA / GAMMA 10-Ks plus a deliberately-low-quality `garbage_10K_2024.pdf` for the rubric-exclusion test (SC-006). No EDGAR dependency in CI.
- **Staged deploy with chicken-egg resolution** — `scripts/bootstrap-dev.sh` orchestrates foundation → data production → consumers so a fresh workspace deploys cleanly with no "errors tolerated."
- **Lakehouse Monitoring + AI/BI dashboard** — drift on extraction confidence, p95 latency by company, ungrounded-answer rate.

## Readiness levels

| Level | Meaning | Required evidence |
|---|---|---|
| Reference-ready | Synthetic corpus deploys and demonstrates the architecture end-to-end | Dev bundle validates, bootstrap succeeds, synthetic CLEARS passes |
| Pilot-ready | Real 10-K filings validate parse/extract/retrieval behavior | Reference-ready + small real EDGAR corpus + reviewed costs/latency |
| Production-ready | Analysts can use it under governed identity and operational SLOs | Pilot-ready + app-level OBO enabled, audit proof, alerts/dashboards, rollback tested |

Full checklists in [`PRODUCTION_READINESS.md`](./PRODUCTION_READINESS.md).

---

## Prerequisites

### Software

| Tool | Version | Why |
|---|---|---|
| Python | 3.11 or 3.12 | Agent + app runtime; tests; eval gate |
| Databricks CLI | ≥ 0.298 | DAB `--strict` validation, `bundle run` for apps, UC permissions API, Lakebase + serving-endpoint resource schemas |
| Git | any recent | Repo + Spec-Kit commit hooks |
| `jq` | any recent | Workspace ID discovery in step 2 of Getting Started (CLI-only fallback shown inline if you don't have it) |
| `make` (optional) | any | Convenience targets if you choose to add them |

macOS install:

```bash
brew install python@3.12 jq
brew install databricks/tap/databricks
```

Linux: see [Databricks CLI install docs](https://docs.databricks.com/aws/en/dev-tools/cli/install).

### Databricks workspace

You need a workspace with **all** of the following enabled:

- Serverless SQL warehouse (AI Functions GA — `ai_parse_document`, `ai_classify`, `ai_extract`, `ai_query`)
- Mosaic AI Vector Search (endpoint + Delta-Sync index)
- Mosaic AI Agent Framework (`databricks-agents`)
- Mosaic AI Model Serving (CPU instances; AI Gateway)
- Lakebase Postgres (preview / GA depending on region)
- Databricks Apps (Streamlit runtime)
- Lakehouse Monitoring
- Unity Catalog with permission to create catalogs/schemas/volumes (or an existing schema you can write to)

**Optional** but recommended for production-tier OBO:

- Databricks Apps **user token passthrough** (workspace admin setting). Without it, the app falls back to service-principal auth — see [`SECURITY.md`](./SECURITY.md).

### Free trial signup

Don't have a workspace? The fastest path is the **14-day Premium trial** at <https://databricks.com/try-databricks>. Verify each entitlement above is enabled in your trial workspace and region — Mosaic AI Vector Search, Lakebase, Databricks Apps, and Model Serving rollout varies by cloud and region, so a Premium tier doesn't automatically guarantee every feature is on. Workspace settings → Previews / Compute → Mosaic AI is the place to check.

> Note: **Free Edition** at databricks.com/learn/free-edition does not include Mosaic AI Vector Search or Model Serving and **cannot run this reference**. Use the Premium trial.

After signup:

```bash
databricks auth login --host https://<your-workspace-host>.cloud.databricks.com
databricks auth profiles   # verify the DEFAULT profile is configured
```

---

## Getting started

### 1. Clone and install

```bash
git clone https://github.com/<your-fork>/databricks-document-intelligence-agent.git
cd databricks-document-intelligence-agent
python -m venv .venv
.venv/bin/pip install -r agent/requirements.txt -r evals/requirements.txt
```

### 2. Discover your workspace IDs

```bash
# With jq:
databricks warehouses list --output json | jq '.[] | {id, name, state}'

# Without jq (CLI-only fallback):
databricks warehouses list
```

Pick the ID of a serverless warehouse (state can be `STOPPED` — it auto-starts). You'll need it as `DOCINTEL_WAREHOUSE_ID`.

### 3. Validate the bundle

```bash
databricks bundle validate --strict -t dev
```

If this prints `Validation OK!`, every YAML resource is schema-correct.

### 4. First-time stand-up (staged bootstrap, ~15–25 min)

```bash
DOCINTEL_CATALOG=workspace \
DOCINTEL_SCHEMA=docintel_10k_dev \
DOCINTEL_WAREHOUSE_ID=<from-step-2> \
./scripts/bootstrap-dev.sh
```

The script handles the chicken-egg ordering automatically — see [Deploy ordering](#deploy-ordering-foundation--consumers).

### 5. Run the eval gate

```bash
DOCINTEL_CATALOG=workspace DOCINTEL_SCHEMA=docintel_10k_dev \
.venv/bin/python evals/clears_eval.py \
  --endpoint analyst-agent-dev \
  --dataset evals/dataset.jsonl
```

Exit 0 means every CLEARS axis met its threshold.

### 6. Open the app

In the workspace UI: **Apps → `doc-intel-analyst-dev`**. Ask:

> What were the top 3 risk factors disclosed by ACME in their FY24 10-K?

You should see a grounded answer with citation chips linking to `ACME_10K_2024.pdf` / `Risk`.

### 7. Steady-state deploys

After the first bring-up, iteration depends on what changed:

```bash
# YAML / pipeline / job / app config changes
databricks bundle deploy -t dev
databricks bundle run -t dev analyst_app                      # apply app config + restart

# Agent code changes (agent/*.py): register a new model version
# and repoint the existing serving endpoint in-place.
DOCINTEL_CATALOG=workspace DOCINTEL_SCHEMA=docintel_10k_dev \
  python agent/log_and_register.py --target dev --serving-endpoint analyst-agent-dev

# Pipeline SQL changes that need to re-process existing filings
databricks bundle run -t dev doc_intel_pipeline
```

You can also re-run `./scripts/bootstrap-dev.sh` — it auto-detects steady-state and does the full cycle (deploy → refresh data → register/promote → app run → grants → smoke) in one command.

For a guided 30-minute tour, see [`specs/001-doc-intel-10k/quickstart.md`](./specs/001-doc-intel-10k/quickstart.md).

---

## Architecture

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

**Triggering**: prod runs the pipeline in `continuous: true` mode so Auto Loader (`read_files`) reacts to new PDFs in the volume automatically. Dev overrides to `continuous: false` to avoid a 24/7 cluster during smoke iterations. See `resources/foundation/doc_intel.pipeline.yml` and the dev override block in `databricks.yml`.

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

**Ownership note**: DAB manages the Vector Search **endpoint** (`resources/consumers/filings_index.yml`) and the index-refresh **job** (`resources/consumers/index_refresh.job.yml`). The **index** itself isn't yet a DAB-managed resource type as of CLI 0.298 — `jobs/index_refresh/sync_index.py` creates the Delta-Sync index on first run and triggers a sync on subsequent runs. That's why the bootstrap script's stage-2 deploy creates the endpoint + job, and the job's first execution materializes the actual index.

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

The agent is an `mlflow.pyfunc` model registered in Unity Catalog and served behind an **AI Gateway** (rate limiting per-user, usage tracking, inference-table audit). Identity passthrough is implemented at the *App layer* when the workspace has Databricks Apps user-token passthrough enabled: the Streamlit app extracts the user's `x-forwarded-access-token` header and constructs a user-scoped `WorkspaceClient`. The served model is OBO-ready via MLflow `auth_policy` and Model Serving user credentials. If app-level passthrough is not enabled, the app falls back to service-principal auth and the repo must be treated as a reference/dev deployment, not a production row-level-security deployment. See [`SECURITY.md`](./SECURITY.md) and `app/README.md`.

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

   OBO (user identity end-to-end, when enabled):
   ──────────────────────────────
   App reads `x-forwarded-access-token` from the request, builds
   `WorkspaceClient(token=...)`, calls the serving endpoint with the
   user's identity. The agent-side MLflow auth policy and Model Serving
   OBO credentials let downstream calls run as the user. If the app-side
   feature is unavailable, the bootstrap script prints an explicit warning
   and the deployment remains reference/dev only.
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

This repo was built with Databricks-specific Claude Code skill bundles. Those bundles are distributed by Databricks via the CLI / Claude Code plugin channel and **are not vendored in this open-source tree** — install them locally if you have access, or reference the canonical Databricks docs (mapping in [`CONTRIBUTING.md`](./CONTRIBUTING.md)).

| Skill bundle | What it provides | Canonical docs |
|---|---|---|
| **databricks-core** | Auth, profiles, data exploration, bundle basics | [docs](https://docs.databricks.com/aws/en/dev-tools/cli/) |
| **databricks-dabs** | DAB structure, validation, deploy workflow, target separation | [docs](https://docs.databricks.com/aws/en/dev-tools/bundles/) |
| **databricks-pipelines** | Lakeflow Spark Declarative Pipelines (`ai_parse_document`, `ai_classify`, `ai_extract`, `APPLY CHANGES INTO`) | [docs](https://docs.databricks.com/aws/en/dlt/) |
| **databricks-jobs** | Lakeflow Jobs with retries, schedules, table-update / file-arrival triggers | [docs](https://docs.databricks.com/aws/en/jobs/) |
| **databricks-apps** | Databricks Apps (Streamlit), App resource bindings | [docs](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/) |
| **databricks-lakebase** | Lakebase Postgres instances, branches, computes, endpoint provisioning | [docs](https://docs.databricks.com/aws/en/oltp/) |
| **databricks-model-serving** | Model Serving endpoints, AI Gateway, served entities, scaling config | [docs](https://docs.databricks.com/aws/en/machine-learning/model-serving/) |

Skills are loaded by Claude Code on demand. When you ask Claude to "wire up Vector Search," it should read the Databricks pipeline/model-serving guidance *before* writing YAML, so the output reflects current Databricks API shapes — not stale training data.

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

## Deploy ordering: foundation → consumers

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

The fix is a **staged deploy** orchestrated by `scripts/bootstrap-dev.sh`. Resources are split into two directories by data dependency:

```
   resources/
   ├── foundation/        ← no data deps — deploy first
   │   ├── catalog.yml             (schema + volume + grants)
   │   ├── doc_intel.pipeline.yml
   │   ├── retention.job.yml
   │   └── lakebase_instance.yml
   │
   └── consumers/         ← need foundation to be RUNNING and producing data
       ├── agent.serving.yml     (needs registered model version)
       ├── kpi_drift.yml         (needs gold_filing_kpis table)
       ├── filings_index.yml     (VS endpoint)
       ├── index_refresh.job.yml (needs source table)
       ├── analyst.app.yml       (needs Lakebase + agent endpoint)
       ├── usage.dashboard.yml
       └── lakebase_catalog.yml  (needs instance AVAILABLE)
```

**The bootstrap script auto-detects which mode to run** by checking whether the agent serving endpoint already has a populated config:

```
                       does analyst-agent-${target} have served entities?
                                     │
                          no ◀───────┴───────▶ yes
                          │                     │
                          ▼                     ▼
                ┌──────────────────┐   ┌──────────────────┐
                │  FIRST-DEPLOY    │   │  STEADY-STATE    │
                │  (staged)        │   │  (full deploy)   │
                ├──────────────────┤   ├──────────────────┤
                │ 1. temp-rename   │   │ 1. bundle deploy │
                │    consumers/*   │   │    (full bundle) │
                │    .yml.skip     │   │                  │
                │ 2. bundle deploy │   │ 2. refresh data: │
                │    (foundation)  │   │    upload, run   │
                │ 3. produce data: │   │    pipeline,     │
                │    upload, run,  │   │    register new  │
                │    register      │   │    model version │
                │    model         │   │    + repoint     │
                │ 4. wait Lakebase │   │    serving in-   │
                │    AVAILABLE     │   │    place         │
                │ 5. restore yamls │   │                  │
                │ 6. bundle deploy │   │                  │
                │    (full bundle) │   │                  │
                └────────┬─────────┘   └────────┬─────────┘
                         │                       │
                         └───────────┬───────────┘
                                     ▼
                         ┌──────────────────────────┐
                         │  Common to both:         │
                         │  • bundle run analyst_app│
                         │  • UC grants chain       │
                         │  • smoke check           │
                         └──────────────────────────┘
```

**Why two modes?** DAB tracks resource state; if you run the temp-rename trick against an *existing* deployment, DAB sees the consumer YAMLs as removed and plans to **delete** the serving endpoint, app, monitor, etc. Safe-ish on a fresh workspace; destructive in steady-state. The script detects mode and does the right thing.

CI (`.github/workflows/deploy.yml`) assumes steady-state — the first-ever bring-up of a workspace must be done locally with `./scripts/bootstrap-dev.sh`. After that, every push to `main` runs the steady-state path: full `bundle deploy` → refresh data → repoint serving endpoint → grants → CLEARS gate.

Full breakdown in [`docs/runbook.md`](./docs/runbook.md).

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

Implementation uses `mlflow.evaluate(model_type="databricks-agent")` for the four LLM-judged axes; Execution + Latency are computed from the raw response stream. Per-row Correctness is sliced from `result.tables['eval_results']` for the SC-002/SC-003 P2 vs P3 thresholds.

---

## Configuration

### Bundle variables (`databricks.yml`)

| Variable | Default | Purpose |
|---|---|---|
| `catalog` | `workspace` | UC catalog for all resources |
| `schema` | `docintel_10k` (prod) / `docintel_10k_dev` (dev) | Schema under the catalog |
| `lakebase_instance` | per-target | Lakebase database instance name |
| `lakebase_stopped` | `false` | Flip to `true` only after instance exists |
| `service_principal_id` | `""` | **Required** for `-t prod`; `bundle validate -t prod` fails loudly without it |
| `warehouse_id` | looked up from `Serverless Starter Warehouse` | Used by index-refresh + dashboards |
| `embedding_model_endpoint_name` | `databricks-bge-large-en` | Vector Search embeddings |
| `foundation_model_endpoint_name` | `databricks-meta-llama-3-3-70b-instruct` | Agent answer generation |
| `rerank_model_endpoint_name` | `databricks-bge-rerank-v2` | Mosaic re-ranker |
| `quality_threshold` | `22` | Section quality cutoff (0-30) for index inclusion |
| `top_k` | `5` | Citations returned after re-rank |
| `max_pdf_bytes` | `52428800` (50 MB) | Reject filings larger than this |
| `analyst_group` | `account users` | UC group granted SELECT/USE on schema, READ/WRITE on volume |

Override via `--var name=value` on any `bundle` command.

### Environment variables (bootstrap + CI)

| Variable | Required | Used by |
|---|---|---|
| `DOCINTEL_CATALOG` | yes | Bootstrap, CI, eval |
| `DOCINTEL_SCHEMA` | yes | Same |
| `DOCINTEL_WAREHOUSE_ID` | yes | Bootstrap kpi-poll, eval slicer |
| `DOCINTEL_TARGET` | no (default `dev`) | Bootstrap |
| `DOCINTEL_ANALYST_GROUP` | no (default `account users`) | UC grants in bootstrap + CI |
| `DOCINTEL_WAIT_SECONDS` | no (default 600) | Bootstrap KPI-table poll timeout |
| `DOCINTEL_LAKEBASE_TIMEOUT` | no (default 600) | Bootstrap Lakebase-AVAILABLE poll |
| `DATABRICKS_HOST` / `DATABRICKS_TOKEN` | yes (CI only) | GitHub Actions auth |

---

## Testing & validation

```bash
# Unit tests (18 tests covering retrieval, agent routing, supervisor)
.venv/bin/python -m pytest agent/tests/ -q

# Bundle schema + interpolation
databricks bundle validate --strict -t dev
databricks bundle validate --strict -t prod   # expected to FAIL without --var service_principal_id (intended safety)

# Bash syntax
bash -n scripts/bootstrap-dev.sh

# Compile checks for all modified Python
.venv/bin/python -m py_compile \
  agent/_obo.py agent/analyst_agent.py agent/log_and_register.py \
  agent/retrieval.py agent/supervisor.py agent/tools.py \
  app/app.py app/lakebase_client.py \
  evals/clears_eval.py scripts/wait_for_kpis.py samples/synthesize.py
```

End-to-end is exercised by [`./scripts/bootstrap-dev.sh`](./scripts/bootstrap-dev.sh) against a real workspace; see [`VALIDATION.md`](./VALIDATION.md) for the full procedure with expected outputs.

---

## Deployment

| Path | When |
|---|---|
| `./scripts/bootstrap-dev.sh` | Fresh-workspace bring-up (or after `bundle destroy`). Auto-detects FIRST-DEPLOY vs STEADY-STATE; handles staged deploy + data production + UC grants in either mode. |
| `databricks bundle deploy -t dev` | YAML / pipeline / job / app config changes after the first bring-up. |
| `databricks bundle run -t dev analyst_app` | After any change to `app/` or `resources/consumers/analyst.app.yml` — required to apply runtime config + restart the app. |
| `python agent/log_and_register.py --target dev --serving-endpoint analyst-agent-dev` | After agent code changes (`agent/*.py`). Registers a new UC model version and repoints the existing serving endpoint in-place. |
| `databricks bundle deploy -t prod --var service_principal_id=<sp-app-id>` | Production deploy, run as the prod SP. |
| GitHub Actions on push to `main` | Steady-state CI: full `bundle deploy` → wait for Lakebase AVAILABLE → upload samples + run pipeline + register/promote agent → UC grants → `bundle run analyst_app` → CLEARS eval gate. (The first-ever bring-up of a workspace must be done locally with `./scripts/bootstrap-dev.sh`.) |

For day-2 ops (rolling agent versions, debugging low quality scores, inspecting CLEARS metrics in MLflow), see [`docs/runbook.md`](./docs/runbook.md). For the production-readiness checklist, see [`PRODUCTION_READINESS.md`](./PRODUCTION_READINESS.md).

---

## Repo layout

```
databricks/
├── databricks.yml                 # Bundle root — variables + dev/prod targets
├── README.md                      # This file
├── CLAUDE.md                      # Runtime guidance for Claude Code sessions
├── CONTRIBUTING.md                # Contribution guidelines
├── SECURITY.md                    # Identity modes, OBO, grants
├── PRODUCTION_READINESS.md        # Reference / Pilot / Production checklists
├── VALIDATION.md                  # Validation procedure with expected outputs
├── REAL_10K_PILOT.md              # Real EDGAR pilot guidance
├── LICENSE                        # MIT
│
├── pipelines/sql/                 # Lakeflow SDP — Bronze → Silver → Gold (SQL)
│   ├── 01_bronze.sql              # Auto Loader BINARYFILE ingest + size filter
│   ├── 02_silver_parse.sql        # ai_parse_document → VARIANT
│   ├── 03_gold_classify_extract.sql  # ai_classify + ai_extract → typed KPIs
│   └── 04_gold_quality.sql        # 5-dim rubric → embed_eligible filter
│
├── agent/                         # Mosaic AI Agent Framework
│   ├── analyst_agent.py           # mlflow.pyfunc model + routing
│   ├── retrieval.py               # Hybrid search + re-rank + OBO VS client
│   ├── supervisor.py              # Cross-company fan-out
│   ├── tools.py                   # UC Function tool over gold_filing_kpis
│   ├── _obo.py                    # On-behalf-of credentials helpers
│   ├── log_and_register.py        # Register + auth_policy + alias
│   └── tests/                     # pytest unit tests
│
├── app/                           # Streamlit App on Databricks Apps
│   ├── app.py                     # Chat UI + citations + thumbs feedback + OBO
│   ├── lakebase_client.py         # psycopg writes to query_logs / feedback
│   ├── app.yaml                   # App runtime config (port, CORS, XSRF)
│   └── README.md                  # App-specific runtime + local-dev notes
│
├── evals/                         # MLflow CLEARS eval gate
│   ├── dataset.jsonl              # 30 hand-authored questions (P2 + P3)
│   └── clears_eval.py             # mlflow.evaluate(model_type="databricks-agent")
│
├── jobs/                          # Lakeflow Jobs Python tasks
│   ├── retention/prune_volume.py  # 90-day raw PDF cleanup
│   └── index_refresh/sync_index.py  # Vector Search SYNC INDEX
│
├── resources/                     # DAB resources, split by data dependency
│   ├── foundation/                # Stage 1 — no data deps
│   └── consumers/                 # Stage 2 — depend on foundation data
│
├── scripts/                       # Operational scripts
│   ├── bootstrap-dev.sh           # Fresh-workspace bring-up (staged deploy)
│   └── wait_for_kpis.py           # Poll helper used by bootstrap + CI
│
├── samples/                       # Synthetic 10-Ks for smoke tests + eval
│   ├── synthesize.py              # Reproducible PDF generator
│   ├── ACME_10K_2024.pdf
│   ├── BETA_10K_2024.pdf
│   ├── GAMMA_10K_2024.pdf
│   └── garbage_10K_2024.pdf       # SC-006 negative test (low quality)
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
└── .github/workflows/
    └── deploy.yml                 # PR validate; main → steady-state deploy + CLEARS gate
                                   # (first-ever bring-up must be done locally via bootstrap-dev.sh)
```

---

## What you can learn from this repo

- **How to wire `ai_parse_document` into Lakeflow SDP** — pattern for streaming-tables + `STREAM(...)` views + `APPLY CHANGES INTO` keyed on filename.
- **How to score document quality before retrieval** — five 0–6 dimensions in SQL, threshold filter on the index source.
- **How to log a Mosaic AI agent to UC** — `mlflow.pyfunc` with both inputs *and* outputs in the signature (UC requirement), `AnyType` for variable-shape fields, `auth_policy` + `resources` for OBO.
- **How to ground an agent with citations** — hybrid Vector Search → re-rank → top-k → LLM with explicit "cite sources [1] [2]" prompt.
- **How to handle DAB deploy ordering** — chicken-egg dependencies between heterogeneous resources, solved with a 5-step bootstrap rather than `depends_on` (which DAB doesn't reliably honor across resource types).
- **How to gate deploys on MLflow eval** — `mlflow.evaluate(model_type="databricks-agent")` with documented metric keys, per-axis thresholds, exit-code gate in CI.
- **How to do end-to-end OBO** — `ModelServingUserCredentials` from `databricks_ai_bridge`, `CredentialStrategy.MODEL_SERVING_USER_CREDENTIALS` for Vector Search, MLflow `auth_policy` with `model-serving` + `vector-search` user scopes, App-side `user_api_scopes` declaration.
- **How Spec-Kit + Claude Code + Databricks skills compose** — every artifact in `specs/` and `pipelines/` and `agent/` was generated through that loop.

---

## Limitations

This is a **pilot-scale** reference implementation, not a turnkey production deployment:

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
| OBO end-to-end | Requires workspace-level `Databricks Apps - user token passthrough` feature | [`SECURITY.md`](./SECURITY.md) |

Latency SLOs: P95 ≤ 8s for single-filing, ≤ 20s for cross-company. End-to-end pipeline ≤ 10 min P95 on a 30 MB PDF.

---

## Contributing

Bug reports, doc fixes, and pattern improvements are welcome. The constitution at [`.specify/memory/constitution.md`](./.specify/memory/constitution.md) defines what the project will and won't accept; PRs that conflict need a constitution amendment first.

See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for local setup, the spec-kit workflow, skill alignment expectations, and the deploy-ordering gotchas reviewers will check for.

## Security

See [`SECURITY.md`](./SECURITY.md) for the identity model (App SP fallback vs end-to-end OBO), required UC grants, secrets-handling guidance, and how to report security issues in a fork or deployment.

## License

Released under the [**MIT License**](./LICENSE) — Copyright (c) 2026 Sathish Krishnan. Use it, fork it, learn from it; just keep the copyright notice.

## Acknowledgments

- [**Spec-Kit**](https://github.com/github/spec-kit) — spec-driven development workflow for AI coding agents.
- [**Claude Code**](https://claude.com/claude-code) — Anthropic's CLI for AI-assisted development.
- [**Anthropic Skills**](https://github.com/anthropics/skills) — general-purpose Claude Code skill bundles.
- [**Databricks Lakehouse + Mosaic AI**](https://www.databricks.com/) — Unity Catalog, Lakeflow Spark Declarative Pipelines, Mosaic AI Vector Search, Agent Framework, Model Serving, AI Gateway, Databricks Apps, Lakebase, Lakehouse Monitoring.

The 10-K analyst pattern is inspired by Databricks's own reference architecture for governed agent applications.
