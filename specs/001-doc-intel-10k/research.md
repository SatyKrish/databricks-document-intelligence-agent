# Phase 0 Research: Databricks 10-K Analyst

The plan's Phase 0 table summarizes decisions; this file holds the longer-form
rationale for the calls that benefit from extra context.

## Decision: `ai_parse_document` over a custom OCR pipeline

**Rationale**: Per Databricks' OfficeQA benchmark, frontier models score below
50% on real-world document QA; pre-processing with `ai_parse_document` adds
~16% accuracy across every tested agent framework, at 5–7× lower cost than
chained OCR + extraction services. It is GA, governed by Unity Catalog,
preserves nested tables and headers, handles scanned/handwritten content, and
emits `VARIANT` for schema-flexible Silver. Constitution principle II
("Parse Once, Extract Many") becomes trivial because subsequent
`ai_classify`/`ai_extract` calls iterate on Silver/Gold without re-parsing.

**Alternatives**:
- *PyPDF2 / Tesseract / LangChain DocumentLoaders*: rejected. No layout
  preservation, no governance, multi-vendor glue, the exact pattern the
  blog identifies as the bottleneck.
- *Azure Document Intelligence + custom UC import*: rejected. Adds external
  dependency, breaks UC lineage at the boundary.

## Decision: SQL pipelines via Lakeflow SDP, not Python

**Rationale**: Constitution principle III. Pipeline DAG is small and
SQL-friendly; using SQL with `ai_*` functions keeps the parse/classify/
extract layer fully declarative. Python helpers would re-introduce
imperative state into the Bronze→Gold path. Lakeflow SDP also handles CDC
(`APPLY CHANGES INTO`) for idempotency on `filename`, retries, and schema
evolution natively.

**Alternatives**:
- *PySpark in DLT*: rejected for parse/classify/extract; reserved for the
  agent + app layer per the language split.
- *Spark on Lakeflow Jobs without SDP*: rejected — no managed materialized-view
  semantics or built-in expectation-based quality enforcement.

## Decision: Quality rubric as SQL `ai_query` calls in Gold

**Rationale**: Reffy's 31-point rubric showed that scoring + filtering at
ingest is more cost-effective than trying to repair context at inference. Using
`ai_query()` in `04_gold_quality.sql` keeps the rubric declarative and
versionable in git; the 5 dimensions (parse_completeness, layout_fidelity,
ocr_confidence, section_recognizability, kpi_extractability) each give
debuggable failure reasons. Threshold at 22/30 (~73%) is conservative and
tunable as a bundle parameter.

**Alternatives**:
- *Single `extraction_confidence` value*: rejected — collapses debuggability.
- *Python scorer in a job*: rejected — imperative, principle III.

## Decision: Agent Bricks Knowledge Assistant + Supervisor Agent, not custom pyfunc

**Rationale**: The Agent Bricks article is explicit that the challenge is not
building an agent loop; it is running agents with real context, permissions,
identity, audit, and control. Therefore the reference implementation must use
Agent Bricks as the agent construction and governance layer. Knowledge
Assistant handles cited single-filing document Q&A over the governed Document
Intelligence output. Supervisor Agent orchestrates Knowledge Assistant with a
deterministic KPI tool over `gold_filing_kpis` for structured comparisons.
AI Gateway and Unity Catalog enforce identity, permissions, audit, routing,
and guardrails end to end.

Custom `mlflow.pyfunc` agents, custom retrieval/reranking loops, custom
supervisor routing, and bespoke Model Serving endpoint ownership are rejected
for this reference because they reproduce the exact glue layer that Agent
Bricks is meant to remove. They also caused deploy-order and serving
provisioning failures during validation.

**Alternatives**:
- *Custom `mlflow.pyfunc` analyst agent*: rejected. It can work as a prototype,
  but it is not the Agent Bricks pattern and it requires custom auth policy,
  serving and supervisor lifecycle code.
- *LangGraph standalone*: rejected. Agent Bricks can interoperate with external
  frameworks when needed, but this reference is intended to demonstrate
  Databricks' governed enterprise agent platform, not a framework-specific
  custom runtime.
- *DSPy* (Reffy's choice): rejected for v1 of this reference. The objective is
  to demonstrate Document Intelligence + Agent Bricks as described in the
  source articles.

## Decision: Lakebase Postgres for conversation state, not Delta tables

**Rationale**: Per-turn writes (~100s of tiny rows/sec at peak) are a poor
fit for Delta. Lakebase Postgres is the platform-native managed Postgres
that integrates with Apps; reads/writes are sub-10ms.
The Reffy team explicitly chose Lakebase for this exact pattern.

## Decision: Streamlit App, not React + FastAPI

**Rationale**: v1 ship-speed. Streamlit on Databricks Apps requires no
frontend build, no separate backend, and the same Python code runs locally
under `databricks` CLI auth and inside the deployed App. React + FastAPI
(Reffy's choice) is the better long-term pattern; deferred to v2.

## Decision: GitHub Actions for CI

**Rationale**: User's existing host. The workflow has two jobs: `validate`
on every PR (`databricks bundle validate -t demo`), and `deploy` on push to
`main` (`databricks bundle deploy -t demo` → `python evals/clears_eval.py`
→ exit non-zero on threshold failure to block the deploy).

## Decision: 90-day retention via Lakeflow Job, not UC volume lifecycle

**Rationale**: UC volume lifecycle policies require workspace-admin policy
edits we can't assume. A Lakeflow Job listing the volume, filtering
`ingested_at < now()-90d`, and removing files is bundle-managed,
auditable in `query_logs`, and trivial to extend later. Silver/Gold are
preserved indefinitely so cited document answers do not lose context after raw cleanup.

## Open follow-ups

None blocking. Items intentionally deferred:

- Lakeflow Connect SharePoint connector (post-v1).
- Content-hash idempotency key (filename suffices for v1).
- React + FastAPI frontend.
