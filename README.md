# Databricks Document Intelligence Agent — Reference Implementation

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Databricks CLI ≥0.298](https://img.shields.io/badge/Databricks_CLI-%E2%89%A50.298-orange)](https://docs.databricks.com/aws/en/dev-tools/cli/install)
[![Status: reference](https://img.shields.io/badge/status-reference%20implementation-informational)](./PRODUCTION_READINESS.md)
[![Built with Spec-Kit](https://img.shields.io/badge/built%20with-Spec--Kit-purple)](https://github.com/github/spec-kit)
[![Built with Claude Code](https://img.shields.io/badge/built%20with-Claude%20Code-D97757)](https://claude.com/claude-code)

A **Databricks-native document intelligence + agent** stack: parse PDFs once with `ai_parse_document`, classify and extract structured KPIs with `ai_classify` / `ai_extract`, score quality on a 5-dimension rubric, index high-quality summaries into Mosaic AI Vector Search, and serve a cited-answer agent through a Streamlit app on Databricks Apps. **Demonstrated on synthetic SEC 10-K filings**, but the architecture works for any structured document corpus (contracts, invoices, research reports, regulatory filings).

> [!IMPORTANT]
> Open-source **reference implementation** for production-grade Databricks patterns. Read [`PRODUCTION_READINESS.md`](./PRODUCTION_READINESS.md), [`SECURITY.md`](./SECURITY.md), and [`VALIDATION.md`](./VALIDATION.md) before pointing real users at it.

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

For architecture and deploy ordering, see [**`docs/design.md`**](./docs/design.md). For operations, validation, and troubleshooting, see [**`docs/runbook.md`**](./docs/runbook.md).

---

## Table of contents

- [Features](#features)
- [Readiness levels](#readiness-levels)
- [How Agent Bricks is used](#how-agent-bricks-is-used)
- [Prerequisites](#prerequisites)
- [Getting started](#getting-started)
- [CLEARS quality gate](#clears-quality-gate)
- [Configuration](#configuration)
- [Testing & validation](#testing--validation)
- [Deployment](#deployment)
- [Repo layout](#repo-layout)
- [Limitations](#limitations)
- [Contributing](#contributing)
- [Security](#security)
- [License](#license)
- [Acknowledgments](#acknowledgments)

---

## Features

- **End-to-end document intelligence pipeline** — Auto Loader ingest → `ai_parse_document` → section explosion → `ai_classify` + `ai_extract` → 5-dim quality rubric → Vector Search Delta-Sync index (the endpoint is DAB-managed; the index is created/synced by `jobs/index_refresh/sync_index.py`). SQL-only pipeline (Lakeflow Spark Declarative Pipelines).
- **Cited-answer agent** — Agent Bricks Knowledge Assistant for cited document Q&A, Supervisor Agent for cross-company orchestration, and a deterministic KPI tool for structured comparisons.
- **Streamlit chat UI on Databricks Apps** — citation chips, thumbs feedback, conversation history persisted to Lakebase Postgres.
- **Eval-gated promotion** — `mlflow.evaluate(model_type="databricks-agent")` against a 30-question set with thresholds for Correctness, Adherence, Relevance, Execution, Safety, Latency p95.
- **Reproducible synthetic corpus** — `samples/synthesize.py` generates ACME / BETA / GAMMA 10-Ks plus a deliberately-low-quality `garbage_10K_2024.pdf` for the rubric-exclusion test (SC-006). No EDGAR dependency in CI.
- **Staged deploy with chicken-egg resolution** — `scripts/bootstrap-demo.sh` orchestrates foundation → data production → consumers so a fresh workspace deploys cleanly with no "errors tolerated."
- **Lakehouse Monitoring + AI/BI dashboard** — drift on extraction confidence, p95 latency by company, ungrounded-answer rate.

## Readiness levels

| Level | Meaning | Required evidence |
|---|---|---|
| Reference-ready | Synthetic corpus deploys and demonstrates the architecture end-to-end | Demo bundle validates, bootstrap succeeds, synthetic CLEARS passes |
| Pilot-ready | Real 10-K filings validate parse/extract/cited-answer behavior | Reference-ready + small real EDGAR corpus + reviewed costs/latency |
| Production-ready | Analysts can use it under governed identity and operational SLOs | Pilot-ready + app-level OBO enabled, audit proof, alerts/dashboards, rollback tested |

Full checklists in [`PRODUCTION_READINESS.md`](./PRODUCTION_READINESS.md).

> Latest demo status, 2026-04-26: Agent Bricks bootstrap and direct Supervisor endpoint smoke passed. Reference-ready remains blocked by Databricks Apps user-token passthrough and CLEARS thresholds. See [`VALIDATION.md`](./VALIDATION.md).

---

## How Agent Bricks is used

Databricks creation path: [Create an AI agent](https://docs.databricks.com/aws/en/generative-ai/agent-framework/create-agent) → Knowledge Assistant for document Q&A, with Supervisor Agent coordinating hosted tools.

The production agent path is:

1. `jobs/index_refresh/sync_index.py` creates/syncs the Mosaic AI Vector Search Delta-Sync index over `gold_filing_sections_indexable`.
2. `agent/agent_bricks.py` creates or updates the Agent Bricks Knowledge Assistant with that Vector Search index as its knowledge source. The source uses `summary` as the searchable text column and `filename` as the document URI column.
3. The same bootstrap creates or updates the UC SQL function `lookup_10k_kpis`.
4. The bootstrap creates or updates the Agent Bricks Supervisor Agent with two tools: the Knowledge Assistant for cited document Q&A and the UC function for deterministic KPI lookups.
5. Agent Bricks generates concrete serving endpoint names. Resolve the live Supervisor endpoint with `./scripts/resolve-agent-endpoint.sh <target>`.
6. The Databricks App receives the resolved endpoint through the `agent_endpoint_name` bundle variable as `DOCINTEL_AGENT_ENDPOINT`.
7. The app invokes `POST /serving-endpoints/{endpoint}/invocations` directly with the user's OBO token. `WorkspaceClient.serving_endpoints.query()` is not used for Agent Bricks invocation because validation showed it did not preserve the needed Agent Bricks response shape.
8. Knowledge Assistant citations currently arrive as markdown footnotes in Agent Bricks output messages. `app/agent_bricks_response.py` normalizes the final answer and extracts citation chips from those footnotes.

Useful Databricks references:

- [Create an AI agent](https://docs.databricks.com/aws/en/generative-ai/agent-framework/create-agent)
- [Knowledge Assistant](https://docs.databricks.com/aws/en/generative-ai/agent-bricks/knowledge-assistant)
- [Supervisor Agent](https://docs.databricks.com/aws/en/generative-ai/agent-bricks/multi-agent-supervisor)

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
- Agent Bricks Knowledge Assistant and Supervisor Agent
- AI Gateway with OBO / identity enforcement
- Lakebase Postgres (preview / GA depending on region)
- Databricks Apps (Streamlit runtime)
- Lakehouse Monitoring
- Unity Catalog with permission to create catalogs/schemas/volumes (or an existing schema you can write to)

**Required for production identity:**

- Databricks Apps **user token passthrough** (workspace admin setting). The app must not fall back to broad service-principal reads — see [`SECURITY.md`](./SECURITY.md).

### Free trial signup

Don't have a workspace? The fastest path is the **14-day Premium trial** at <https://databricks.com/try-databricks>. Verify each entitlement above is enabled in your trial workspace and region — Mosaic AI Vector Search, Lakebase, Databricks Apps, Agent Bricks, and AI Gateway rollout varies by cloud and region, so a Premium tier doesn't automatically guarantee every feature is on. Workspace settings → Previews / Compute → Mosaic AI is the place to check.

> Note: **Free Edition** at databricks.com/learn/free-edition does not include the required governed agent services and **cannot run this implementation**. Use the Premium trial.

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
.venv/bin/pip install -r agent/requirements.txt -r evals/requirements.txt pytest
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
databricks bundle validate --strict -t demo
```

If this prints `Validation OK!`, every YAML resource is schema-correct.

### 4. First-time stand-up (staged bootstrap, ~15–25 min)

```bash
DOCINTEL_CATALOG=workspace \
DOCINTEL_SCHEMA=docintel_10k_demo \
DOCINTEL_WAREHOUSE_ID=<from-step-2> \
./scripts/bootstrap-demo.sh
```

The script handles the chicken-egg ordering automatically — see [`docs/design.md` § Deploy ordering](./docs/design.md#deploy-ordering-foundation--consumers).

### 5. Run the eval gate

```bash
DOCINTEL_CATALOG=workspace DOCINTEL_SCHEMA=docintel_10k_demo \
.venv/bin/python evals/clears_eval.py \
  --endpoint "$(./scripts/resolve-agent-endpoint.sh demo)" \
  --dataset evals/dataset.jsonl
```

Exit 0 means every CLEARS axis met its threshold.

### 6. Open the app

In the workspace UI: **Apps → `doc-intel-analyst-demo`**. Ask:

> What were the top 3 risk factors disclosed by ACME in their FY24 10-K?

You should see a grounded answer with citation chips linking to `ACME_10K_2024.pdf` / `Risk`.

### 7. Steady-state deploys

After the first bring-up, iteration depends on what changed:

```bash
# YAML / pipeline / job / app config changes
AGENT_ENDPOINT_NAME="$(./scripts/resolve-agent-endpoint.sh demo)"
databricks bundle deploy -t demo --var "agent_endpoint_name=${AGENT_ENDPOINT_NAME}"
databricks bundle run -t demo --var "agent_endpoint_name=${AGENT_ENDPOINT_NAME}" analyst_app

# Agent Bricks configuration / tool glue changes
DOCINTEL_CATALOG=workspace \
DOCINTEL_SCHEMA=docintel_10k_demo \
DOCINTEL_WAREHOUSE_ID=<from-step-2> \
python -m agent.agent_bricks --target demo
AGENT_ENDPOINT_NAME="$(./scripts/resolve-agent-endpoint.sh demo)"
databricks bundle deploy -t demo --var "agent_endpoint_name=${AGENT_ENDPOINT_NAME}"
databricks bundle run -t demo --var "agent_endpoint_name=${AGENT_ENDPOINT_NAME}" analyst_app

# Pipeline SQL changes that need to re-process existing filings
databricks bundle run -t demo doc_intel_pipeline
```

You can also re-run `./scripts/bootstrap-demo.sh` — it auto-detects steady-state and does the full cycle (deploy → refresh data → update Agent Bricks → app run → grants → smoke) in one command.

For a guided 30-minute tour, see [`specs/001-doc-intel-10k/quickstart.md`](./specs/001-doc-intel-10k/quickstart.md).

---

## CLEARS quality gate

Before any deploy reaches production, an evaluation must pass (constitution principle V — eval-gated agents).

```
   evals/dataset.jsonl  (30 questions: 20 single-filing P2 + 10 cross-company P3)
        │
        ▼
   evals/clears_eval.py  ──▶  hits the demo endpoint, scores 6 axes:

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

Implementation uses `mlflow.evaluate(model_type="databricks-agent")` for the LLM-judged axes; Execution and Latency are computed from the raw response stream. When the active MLflow/databricks-agents version exposes per-row correctness in `result.tables['eval_results']`, the runner also logs SC-002/SC-003 P2 vs P3 slices. Current 1.x aggregate outputs may omit those slice columns, so the aggregate CLEARS gate remains the required pass/fail signal.

---

## Configuration

### Bundle variables (`databricks.yml`)

| Variable | Default | Purpose |
|---|---|---|
| `catalog` | `workspace` | UC catalog for all resources |
| `schema` | `docintel_10k` (prod) / `docintel_10k_demo` (demo) | Schema under the catalog |
| `lakebase_instance` | per-target | Lakebase database instance name |
| `lakebase_stopped` | `false` | Flip to `true` only after instance exists |
| `service_principal_id` | `""` | **Required** for `-t prod`; `bundle validate -t prod` fails loudly without it |
| `warehouse_id` | looked up from `Serverless Starter Warehouse` | Used by index-refresh + dashboards |
| `embedding_model_endpoint_name` | `databricks-bge-large-en` | Vector Search embeddings |
| `quality_threshold` | `22` | Section quality cutoff (0-30) for index inclusion |
| `max_pdf_bytes` | `52428800` (50 MB) | Reject filings larger than this |
| `analyst_group` | `account users` | UC group granted SELECT/USE on schema, READ/WRITE on volume |
| `agent_endpoint_name` | `UNSET_AGENT_BRICKS_ENDPOINT` | Generated Agent Bricks Supervisor endpoint resolved by `scripts/resolve-agent-endpoint.sh`; pass it on deploy/app-run commands after bootstrap |

Override via `--var name=value` on any `bundle` command.

### Environment variables (bootstrap + CI)

| Variable | Required | Used by |
|---|---|---|
| `DOCINTEL_CATALOG` | yes | Bootstrap, CI, eval |
| `DOCINTEL_SCHEMA` | yes | Same |
| `DOCINTEL_WAREHOUSE_ID` | yes | Bootstrap (passed to bundle as `--var warehouse_id`, used by kpi-poll + smoke); `agent/tools.py` structured KPI tool |
| `DOCINTEL_TARGET` | no (default `demo`) | Bootstrap |
| `DOCINTEL_ANALYST_GROUP` | no (default `account users`) | UC grants in bootstrap + CI |
| `DOCINTEL_WAIT_SECONDS` | no (default 600) | Bootstrap KPI-table poll timeout |
| `DOCINTEL_LAKEBASE_TIMEOUT` | no (default 600) | Bootstrap Lakebase-AVAILABLE poll |
| `DATABRICKS_HOST` / `DATABRICKS_TOKEN` | yes (CI only) | GitHub Actions auth |

---

## Testing & validation

```bash
# Unit tests for Agent Bricks tool glue and app helpers
.venv/bin/python -m pytest agent/tests/ -q

# Bundle schema + interpolation
databricks bundle validate --strict -t demo
databricks bundle validate --strict -t prod   # expected to FAIL without --var service_principal_id (intended safety)

# Bash syntax
bash -n scripts/bootstrap-demo.sh

# Compile checks for all modified Python
.venv/bin/python -m py_compile \
  agent/agent_bricks.py agent/tools.py \
  app/app.py app/agent_bricks_client.py app/agent_bricks_response.py app/lakebase_client.py \
  evals/clears_eval.py \
  scripts/wait_for_kpis.py samples/synthesize.py
```

End-to-end is exercised by [`./scripts/bootstrap-demo.sh`](./scripts/bootstrap-demo.sh) against a real workspace; see [`VALIDATION.md`](./VALIDATION.md) for the full procedure with expected outputs.

---

## Deployment

| Path | When |
|---|---|
| `./scripts/bootstrap-demo.sh` | Fresh-workspace bring-up (or after `bundle destroy`). Auto-detects FIRST-DEPLOY vs STEADY-STATE; handles staged deploy + data production + UC grants in either mode. |
| `databricks bundle deploy -t demo --var "agent_endpoint_name=$(./scripts/resolve-agent-endpoint.sh demo)"` | YAML / pipeline / job / app config changes after the first bring-up. |
| `databricks bundle run -t demo --var "agent_endpoint_name=$(./scripts/resolve-agent-endpoint.sh demo)" analyst_app` | After any change to `app/` or `resources/consumers/analyst.app.yml` — required to apply runtime config + restart the app. |
| `databricks bundle deploy -t prod --var service_principal_id=<sp-app-id> --var "agent_endpoint_name=$(./scripts/resolve-agent-endpoint.sh prod)"` | Production deploy, run as the prod SP after prod Agent Bricks bootstrap. |
| GitHub Actions on push to `main` | Steady-state CI: full `bundle deploy` → wait for Lakebase AVAILABLE → upload samples + run pipeline → Agent Bricks / AI Gateway validation → UC grants → `bundle run analyst_app` → CLEARS eval gate. (The first-ever bring-up of a workspace must be done locally with `./scripts/bootstrap-demo.sh`.) |

For day-2 ops (Agent Bricks configuration validation, debugging low quality scores, inspecting CLEARS metrics in MLflow), see [`docs/runbook.md`](./docs/runbook.md). For the production-readiness checklist, see [`PRODUCTION_READINESS.md`](./PRODUCTION_READINESS.md).

---

## Repo layout

```
databricks/
├── databricks.yml                 # Bundle root — variables + demo/prod targets
├── pipelines/sql/                 # Lakeflow SDP — Bronze → Silver → Gold (SQL only)
├── agent/                         # Agent Bricks deterministic tool glue
├── app/                           # Streamlit on Databricks Apps + Lakebase client
├── evals/                         # MLflow CLEARS eval gate (dataset + runner)
├── jobs/                          # Lakeflow Jobs (retention, index refresh)
├── resources/foundation/          # DAB resources with no data deps
├── resources/consumers/           # DAB resources that depend on foundation data
├── scripts/                       # bootstrap-demo.sh + helpers
├── samples/                       # Synthetic 10-K PDFs (regenerable)
├── specs/001-doc-intel-10k/       # Spec-Kit artifacts (spec, plan, tasks, etc.)
├── docs/                          # design.md (this repo's "why") + runbook.md (day-2 ops)
└── .specify/                      # Spec-Kit machinery (constitution, hooks)
```

Top-level docs: [`CLAUDE.md`](./CLAUDE.md) (runtime guidance for Claude Code), [`CONTRIBUTING.md`](./CONTRIBUTING.md), [`SECURITY.md`](./SECURITY.md), [`PRODUCTION_READINESS.md`](./PRODUCTION_READINESS.md), [`VALIDATION.md`](./VALIDATION.md), [`REAL_10K_PILOT.md`](./REAL_10K_PILOT.md), [`LICENSE`](./LICENSE).

---

## Limitations

This is a production-oriented reference implementation with conservative scale defaults:

| Limit | Value | Source |
|---|---|---|
| Filings in demo | ~500 | spec.md scale |
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

See [`SECURITY.md`](./SECURITY.md) for the mandatory end-to-end OBO identity model, required UC grants, secrets-handling guidance, and how to report security issues in a fork or deployment.

## License

Released under the [**MIT License**](./LICENSE) — Copyright (c) 2026 Sathish Krishnan. Use it, fork it, learn from it; just keep the copyright notice.

## Acknowledgments

- [**Spec-Kit**](https://github.com/github/spec-kit) — spec-driven development workflow for AI coding agents.
- [**Claude Code**](https://claude.com/claude-code) — Anthropic's CLI for AI-assisted development.
- [**Agent Skills**](https://github.com/anthropics/skills) — general-purpose Claude Code skill bundles.
- [**Databricks**](https://www.databricks.com/) — Unity Catalog, Document Intelligence AI Functions, Lakeflow Spark Declarative Pipelines, Mosaic AI Vector Search, Agent Bricks, AI Gateway, Databricks Apps, Lakebase, Lakehouse Monitoring.
