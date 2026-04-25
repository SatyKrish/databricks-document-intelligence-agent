<!--
SYNC IMPACT REPORT
==================
Version change: (initial) → 1.0.0
Modified principles: N/A (initial ratification)
Added sections:
  - Core Principles (6 principles, replacing 5-principle template scaffold)
  - Additional Constraints
  - Development Workflow
  - Governance
Removed sections: none
Templates requiring updates:
  - ✅ .specify/templates/plan-template.md — verified Constitution Check gate aligns
  - ✅ .specify/templates/spec-template.md — no mandatory section changes
  - ✅ .specify/templates/tasks-template.md — no task-category changes
  - ✅ .specify/templates/checklist-template.md — no changes
Follow-up TODOs: none
-->

# Databricks Document Intelligence + Agent Bricks Constitution

## Core Principles

### I. Unity Catalog is the Source of Truth (NON-NEGOTIABLE)

Every table, volume, function, model, vector index, and serving endpoint MUST be
registered in Unity Catalog under a single parameterized `<catalog>.<schema>`
defined in the bundle. Hard-coded paths, workspace-local DBFS resources, and
direct S3/ADLS references outside the bundle are forbidden. All access flows
through UC governance: lineage, ACLs, and audit are mandatory side effects of
correct usage. *Rationale:* governance, lineage, and per-user identity
passthrough only work when UC is the only namespace.

### II. Parse Once, Extract Many

`ai_parse_document` MUST run exactly once per source document, at the Silver
layer, with `VARIANT` output that preserves layout, nested tables, and headers.
`ai_classify`, `ai_extract`, and `ai_prep_search` MUST iterate on Gold and
derived tables — never re-parse the original bytes to add a new field.
*Rationale:* parsing is the dominant cost and latency driver; iteration on
extracted fields is cheap. Re-parsing breaks reproducibility of downstream
extractions.

### III. Declarative Over Imperative

Pipelines MUST be Lakeflow Spark Declarative Pipelines authored in SQL.
Orchestration MUST be Lakeflow Jobs with explicit retry policies and
event-based triggers (table updates, file arrivals). All resources MUST be
defined in Databricks Asset Bundles (`databricks.yml` + `resources/*.yml`).
Ad-hoc notebooks are permitted for exploration ONLY and MUST NOT appear as
production artifacts referenced by jobs, dashboards, or apps. *Rationale:*
declarative resources are diffable, deployable, and observable; imperative
scripts hide state and resist review.

### IV. Quality Before Retrieval

Every Gold-layer row MUST carry a numeric quality score produced by an
explicit, version-controlled rubric. Only rows above a configured threshold
(parameter of the bundle) MUST be embedded into Vector Search. The text
embedded MUST be a curated `summary` column derived from the parsed content,
not the raw chunks themselves. *Rationale:* retrieval quality is bounded
above by ingest quality; embedding noise produces confidently wrong agent
answers. Filtering and summarization at ingest is cheaper than re-ranking at
inference.

### V. Eval-Gated Agents

No agent endpoint MAY be promoted to a `dev` or higher target without MLflow
CLEARS scores — Correctness, Latency, Execution, Adherence, Relevance, Safety
— meeting per-axis thresholds defined in `evals/` on a curated eval set
checked into the repo. Lakehouse Monitoring MUST run on extraction tables to
detect drift in `ai_extract` outputs. An AI/BI dashboard MUST summarize
conversation logs to surface content gaps each iteration. *Rationale:*
agents fail silently and at user expense; the only defense is automated,
gated evaluation plus production telemetry.

### VI. Reproducible Deploys

One `databricks bundle deploy -t <env>` MUST recreate the entire stack
(catalog/schema/volume, pipelines, vector index, agent endpoint, monitors,
dashboards, app). Two environments are defined: `dev` and `prod`. A Service
Principal MUST own `prod` deploys. The same Python code path MUST run locally
(via the unified Databricks CLI auth) and inside Databricks Apps without
environment-specific shims. *Rationale:* click-ops and per-env divergence
are how staging-vs-prod incidents happen; bundles enforce parity.

## Additional Constraints

The technology stack is locked to Databricks-native primitives:

- **Pipelines**: Lakeflow Spark Declarative Pipelines (SQL only for parse/
  classify/extract layers).
- **Orchestration**: Lakeflow Jobs.
- **Retrieval**: Mosaic AI Vector Search (no external vector databases).
- **Agents**: Mosaic AI Agent Framework, with Knowledge Assistant for
  document Q&A and Supervisor Agents for cross-doc fan-out.
- **Serving**: Databricks Model Serving (CPU-first; GPU only with documented
  justification).
- **Gateway**: AI Gateway in front of all agent endpoints (rate limit, audit,
  identity passthrough).
- **UI**: Databricks Apps (Streamlit for v1).
- **State**: Lakebase Postgres for conversation history and feedback.
- **Governance**: Unity Catalog for everything else.

External orchestrators (Airflow, Dagster, Prefect), external vector databases
(Pinecone, Weaviate, etc.), external feature stores, and external LLM
gateways are out of scope and MUST NOT be introduced without a constitution
amendment.

Languages: SQL for parse/classify/extract pipelines; Python for the agent and
the Databricks App. No additional runtime languages.

## Development Workflow

Spec-Kit drives the development cycle: `/speckit-specify` → `/speckit-clarify`
→ `/speckit-plan` → `/speckit-tasks` → `/speckit-analyze` → `/speckit-implement`.
Each phase auto-commits via the hooks defined in `.specify/extensions.yml`.

Pull requests MUST pass `databricks bundle validate -t dev` before merge.
Merge to `main` triggers a GitHub Actions workflow that runs
`databricks bundle deploy -t dev`. Promotion to `prod` is manual, gated on a
tagged release and a successful CLEARS eval run on the dev endpoint.

## Governance

This constitution supersedes all other practices in this repository. Any
deviation in a `spec.md` or `plan.md` MUST be flagged in the Complexity
section of `plan.md` with explicit justification, alternatives considered,
and a sunset commitment.

Amendments require:

1. A pull request that updates this file AND any dependent templates in
   `.specify/templates/`.
2. A version bump per semantic versioning (MAJOR for principle removal/
   redefinition, MINOR for new principle/section, PATCH for clarifications).
3. Approval review confirming the Sync Impact Report at the top of this file
   reflects all template changes.

All PR reviews MUST verify compliance with the principles above. Use
`CLAUDE.md` for runtime development guidance.

**Version**: 1.0.0 | **Ratified**: 2026-04-24 | **Last Amended**: 2026-04-24
