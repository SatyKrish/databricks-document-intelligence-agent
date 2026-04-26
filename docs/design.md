# Design — Databricks Document Intelligence Agent

This document covers the *why*, the architecture, and the build workflow behind the repo. For setup and day-to-day use, see [`README.md`](../README.md). For day-2 ops, see [`runbook.md`](./runbook.md).

## Table of contents

- [Why this exists](#why-this-exists)
- [Architecture](#architecture)
  - [Two halves: an offline pipeline, and an online agent](#two-halves-an-offline-pipeline-and-an-online-agent)
  - [Vector Search bridges data and agent](#vector-search-bridges-data-and-agent)
  - [Agent has two paths, one endpoint](#agent-has-two-paths-one-endpoint)
  - [Runtime stack](#runtime-stack)
- [How it's built — three pillars](#how-its-built--three-pillars)
  - [Pillar 1 — Spec-Kit](#pillar-1--spec-kit-spec-driven-development)
  - [Pillar 2 — Databricks Asset Bundles + the Claude Code skill suite](#pillar-2--databricks-asset-bundles--the-claude-code-skill-suite)
  - [Pillar 3 — Claude Code as the implementation surface](#pillar-3--claude-code-as-the-implementation-surface)
- [Deploy ordering: foundation → consumers](#deploy-ordering-foundation--consumers)
- [What you can learn from this repo](#what-you-can-learn-from-this-repo)

---

## Why this exists

Databricks shipped a lot of new generative-AI surface area in 2025–2026: Document Intelligence (`ai_parse_document`, `ai_classify`, `ai_extract`), Agent Bricks, AI Gateway, Lakebase, and Databricks Apps. The two source articles for this reference are Databricks' Document Intelligence launch article ("Why Your Agents Can't Read Enterprise Documents") and the Agent Bricks platform article. The reference exists to demonstrate those patterns end to end: parse messy enterprise PDFs into a governed document data layer, then build a governed agent on that enriched layer through Agent Bricks.

This repo is that worked example. Drop a PDF into a governed UC volume; ten minutes later, an analyst can ask cited questions in plain English with end-to-end audit. The desired target architecture is **Agent Bricks-first**: Document Intelligence prepares the governed source of truth; Knowledge Assistant handles cited document Q&A; Supervisor Agent coordinates document Q&A with structured KPI tools; AI Gateway, Unity Catalog, OBO, Lakebase, and CLEARS provide the governance and operating layer.

The earlier custom `mlflow.pyfunc` agent path diverged from that target by re-introducing custom serving lifecycle, auth-policy ordering, retrieval, and supervisor code that Agent Bricks is meant to absorb. The production path now uses Agent Bricks bootstrap instead of that custom runtime.

It also demonstrates a development workflow: **Spec-Kit** for spec-driven design, **Claude Code** with Databricks skill bundles for AI-assisted implementation, six **non-negotiable constitution principles** that gate every plan. See [How it's built](#how-its-built--three-pillars).

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
                     │                 │   │ document)       │   │  uses full_      │
                     │  >50MB rejects: │   │                 │   │  document when   │
                     │  bronze_filings │   │ Status: ok /    │   │  sections absent)│
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

**Triggering**: prod runs the pipeline in `continuous: true` mode so Auto Loader (`read_files`) reacts to new PDFs in the volume automatically. Demo overrides to `continuous: false` to avoid a 24/7 cluster during smoke iterations. See `resources/foundation/doc_intel.pipeline.yml` and the demo override block in `databricks.yml`.

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

**Ownership note**: DAB manages the Vector Search **endpoint** (`resources/foundation/filings_index.yml`) and the index-refresh **job** (`resources/consumers/index_refresh.job.yml`). The **index** itself isn't yet a DAB-managed resource type as of CLI 0.298 — `jobs/index_refresh/sync_index.py` creates the Delta-Sync index on first run and triggers a sync on subsequent runs. The endpoint lives in foundation so first-deploy bootstrap can materialize the index before `scripts/bootstrap_agent_bricks.py` attaches it to Knowledge Assistant.

### Agent Bricks target runtime

```
   User question
        │
        ▼
   ┌─────────────────────────────────────────────┐
   │ Agent Bricks Supervisor Agent               │
   │ - owns routing and orchestration            │
   │ - runs under UC / AI Gateway governance     │
   └────────────┬─────────────────────┬──────────┘
                │                     │
                ▼                     ▼
   ┌────────────────────────┐  ┌────────────────────────┐
   │ Knowledge Assistant    │  │ Structured KPI tool    │
   │ - cited document Q&A   │  │ - reads Gold KPI table │
   │ - grounded in parsed   │  │ - deterministic tables │
   │   document layer / VS  │  │   for comparisons      │
   └────────────────────────┘  └────────────────────────┘
                │                     │
                └──────────┬──────────┘
                           ▼
               ┌──────────────────────┐
               │ Response JSON / App  │
               │ citations, feedback  │
               │ latency, audit       │
               └──────────────────────┘
```

Knowledge Assistant is the default single-filing Q&A path because the Agent Bricks article positions the hard part as governed context, identity, and observability rather than hand-building the agent loop. Supervisor Agent is the default cross-company orchestration path. Custom code is allowed only where it is business logic around Agent Bricks, such as a deterministic KPI table tool or the App-specific feedback UI. It must not replace Knowledge Assistant, Supervisor Agent, Agent Bricks serving, or Agent Bricks governance.

**Removed divergence**: the custom `agent/analyst_agent.py`, `agent/retrieval.py`, `agent/supervisor.py`, `agent/log_and_register.py`, and `resources/consumers/agent.serving.yml` path has been removed. `scripts/bootstrap_agent_bricks.py` is now the production bootstrap for Knowledge Assistant, the UC KPI function, and Supervisor Agent configuration.

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
   │ Agent Bricks endpoint  │  │  Lakebase Postgres     │
   │ Knowledge Assistant +  │  │  ─────────────────      │
   │ Supervisor Agent       │  │  conversation_history   │
   │                        │  │  query_logs             │
   │  + AI Gateway:         │  │  feedback               │
   │    OBO, permissions,   │  │                        │
   │    audit, rate limits, │  │  (Postgres for tiny    │
   │    guardrails          │  │   per-turn writes —    │
   │                        │  │   Delta isn't great    │
   │                        │  │   at row-by-row)       │
   └────────────────────────┘  └────────────────────────┘

   OBO (user identity end-to-end, mandatory):
   ──────────────────────────────
   App reads `x-forwarded-access-token` from the request and invokes the
   Agent Bricks endpoint with the user's identity. AI Gateway and Unity
   Catalog enforce identity, permissions, audit, and routing across the
   agent, model, tools, and data. User token passthrough is a hard
   prerequisite for production. If the workspace cannot provide end-to-end
   OBO, deployment must fail rather than silently falling back to a service
   principal identity.
```

**Why Postgres for state?** Delta tables are great for analytics but bad at "insert one tiny row per chat turn at high frequency." Lakebase is Databricks's managed Postgres — same governance, right tool for the job.

---

## How it's built — three pillars

This repo combines three things: Spec-Kit for spec-driven design, Databricks Asset Bundles + Claude Code skill bundles for declarative platform work, and Claude Code as the implementation surface.

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
| V | **Eval-gated Agent Bricks** | CLEARS scores must clear thresholds before any deploy is considered complete |
| VI | **Reproducible deploys** | `databricks bundle deploy -t <env>` recreates the entire stack; `demo` and `prod` parity enforced |

When you read `specs/001-doc-intel-10k/plan.md` you'll see a "Constitution Check" gate that maps each design decision back to the principle it satisfies. When you read `specs/001-doc-intel-10k/tasks.md` you'll see how each task derives from the plan, and how user-stories (P1, P2, P3) are independently demoable.

### Pillar 2 — Databricks Asset Bundles + the Claude Code skill suite

[**Databricks Asset Bundles**](https://docs.databricks.com/aws/en/dev-tools/bundles/) (DABs) describe most of the workspace state as YAML. One root `databricks.yml` declares variables and targets (`demo`, `prod`); `resources/**/*.yml` declares each resource (pipeline, jobs, Vector Search endpoint, index-refresh job, Agent Bricks endpoint/configuration, app, monitor, dashboard, Lakebase instance + catalog). `databricks bundle deploy -t demo` reconciles workspace state to YAML. The Vector Search **index** is still created and synced by `jobs/index_refresh/sync_index.py` until DAB supports index resources directly.

This repo was built with Databricks-specific Claude Code skill bundles. Those bundles are distributed by Databricks via the CLI / Claude Code plugin channel and **are not vendored in this open-source tree** — install them locally if you have access, or reference the canonical Databricks docs (mapping in [`../CONTRIBUTING.md`](../CONTRIBUTING.md)).

| Skill bundle | What it provides | Canonical docs |
|---|---|---|
| **databricks-core** | Auth, profiles, data exploration, bundle basics | [docs](https://docs.databricks.com/aws/en/dev-tools/cli/) |
| **databricks-dabs** | DAB structure, validation, deploy workflow, target separation | [docs](https://docs.databricks.com/aws/en/dev-tools/bundles/) |
| **databricks-pipelines** | Lakeflow Spark Declarative Pipelines (`ai_parse_document`, `ai_classify`, `ai_extract`, `APPLY CHANGES INTO`) | [docs](https://docs.databricks.com/aws/en/dlt/) |
| **databricks-jobs** | Lakeflow Jobs with retries, schedules, table-update / file-arrival triggers | [docs](https://docs.databricks.com/aws/en/jobs/) |
| **databricks-apps** | Databricks Apps (Streamlit), App resource bindings | [docs](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/) |
| **databricks-lakebase** | Lakebase Postgres instances, branches, computes, endpoint provisioning | [docs](https://docs.databricks.com/aws/en/oltp/) |
| **databricks-agent-bricks** | Knowledge Assistant, Supervisor Agent, UC tools, endpoint lifecycle | [docs](https://docs.databricks.com/aws/en/generative-ai/agent-bricks/knowledge-assistant) |

Skills are loaded by Claude Code on demand. When you ask Claude to "wire up Vector Search," it should read the Databricks pipeline/model-serving guidance *before* writing YAML, so the output reflects current Databricks API shapes — not stale training data.

### Pillar 3 — Claude Code as the implementation surface

Spec-Kit produces the specs. The Databricks skills provide platform expertise. **Claude Code orchestrates both**: every phase artifact and every code file in this repo was authored by prompting Claude Code with the spec/plan/tasks as context.

The workflow looks like:

1. `/speckit-specify` → Claude writes spec.md from a natural-language description, you iterate via `/speckit-clarify` until ambiguity is resolved.
2. `/speckit-plan` → Claude consults the constitution + Databricks skills, drafts plan.md with research decisions and architecture.
3. `/speckit-tasks` → Claude generates a dependency-ordered task list grouped by user story (P1, P2, P3).
4. `/speckit-implement` → Claude writes the actual SQL/Python/YAML, one task at a time, committing per task.
5. Operational loops: when the deploy hits unexpected issues (it always does), Claude reads the runbook, fixes the issue, updates the runbook, commits.

AI-driven here means Claude carries the boring parts (boilerplate YAML, retry-loop scripts, dependency analysis) so you spend time on what the spec should say and what the constitution should require.

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
        │   ▸ Agent Bricks ──┤    Monitor wants the      │
        │   ▸ App         ───┤    KPI table to exist     │
        │   ▸ App         ───┤    BEFORE it can attach   │
        │   ▸ Monitor    ────┘                           │
        │   ▸ Lakebase   ────                            │
        └────────────────────────────────────────────────┘

   App needs the Agent Bricks Supervisor endpoint.
        Supervisor needs Knowledge Assistant + UC function tools.
              Knowledge Assistant needs the Vector Search index.
                    Monitor needs the table populated.
                          Table needs the pipeline to run.

   ▶ Single `bundle deploy` → 4+ errors on a fresh workspace.
```

The fix is a **staged deploy** orchestrated by `scripts/bootstrap-demo.sh`. Resources are split into two directories by data dependency:

```
   resources/
   ├── foundation/        ← no data deps — deploy first
   │   ├── catalog.yml             (schema + volume + grants)
   │   ├── doc_intel.pipeline.yml
   │   ├── retention.job.yml
   │   ├── filings_index.yml       (VS endpoint)
   │   └── lakebase_instance.yml
   │
   └── consumers/         ← need foundation to be RUNNING and producing data
       ├── kpi_drift.yml         (needs gold_filing_kpis table)
       ├── index_refresh.job.yml (needs source table)
       ├── analyst.app.yml       (needs Lakebase + agent endpoint)
       ├── usage.dashboard.yml
       └── lakebase_catalog.yml  (needs instance AVAILABLE)
```

**The bootstrap script auto-detects which mode to run** by checking whether the Agent Bricks Supervisor endpoint exists:

```
                       does analyst-agent-${target} exist?
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
                │    upload, run,  │   │    sync index,   │
                │    sync index,   │   │    update Agent  │
                │    Agent Bricks  │   │    Bricks        │
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

**Why two modes?** DAB tracks resource state; if you run the temp-rename trick against an existing deployment, DAB sees the consumer YAMLs as removed and plans to delete the app, monitor, dashboard, etc. Appropriate on a fresh workspace; destructive in steady-state. The script detects mode and does the right thing.

CI (`.github/workflows/deploy.yml`) assumes steady-state — the first-ever bring-up of a workspace must be done locally with `./scripts/bootstrap-demo.sh`. After that, every push to `main` runs the steady-state path: full `bundle deploy` → refresh data → sync index → update Agent Bricks → grants → CLEARS gate.

For the per-step procedure and known failure modes, see [`runbook.md` § Known deploy ordering gaps](./runbook.md#known-deploy-ordering-gaps).

---

## What you can learn from this repo

- **Wiring `ai_parse_document` into Lakeflow SDP** — pattern for streaming-tables + `STREAM(...)` views + `APPLY CHANGES INTO` keyed on filename.
- **Scoring document quality before retrieval** — five 0–6 dimensions in SQL, threshold filter on the index source.
- **Building on Agent Bricks instead of custom agent loops** — Knowledge Assistant for cited document Q&A, Supervisor Agent for orchestration, deterministic KPI tool glue for structured comparisons.
- **Grounding an agent with citations** — Document Intelligence output and the governed Vector Search / Knowledge Assistant source provide the citation-bearing context.
- **Handling DAB deploy ordering** — chicken-egg dependencies between heterogeneous resources, solved with a 5-step bootstrap rather than `depends_on` (which DAB doesn't reliably honor across resource types).
- **Gating deploys on MLflow eval** — `mlflow.evaluate(model_type="databricks-agent")` with documented metric keys, per-axis thresholds, exit-code gate in CI.
- **End-to-end OBO** — Databricks Apps user-token passthrough, Agent Bricks / AI Gateway identity enforcement, UC permissions, and audit verification are production prerequisites.
- **Spec-Kit + Claude Code + Databricks skills composing** — every artifact in `specs/` and `pipelines/` and `agent/` was generated through that loop.
