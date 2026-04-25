# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository status

Active feature: **001-doc-intel-10k** — Databricks Document Intelligence + Agent Bricks 10-K Analyst.
Drives a Bronze→Silver→Gold pipeline (`ai_parse_document` / `ai_classify` / `ai_extract`),
Mosaic AI Vector Search index, agent endpoint behind AI Gateway, Streamlit App on Databricks Apps,
Lakebase state, Lakehouse Monitoring, and an MLflow CLEARS eval gate — all in one DAB.

## Build & deploy

- Validate: `databricks bundle validate -t dev`
- Deploy: `databricks bundle deploy -t dev`
- Run pipeline: `databricks bundle run -t dev doc_intel_pipeline`
- Run eval: `python evals/clears_eval.py --endpoint analyst-agent-dev`

## Spec-Kit cycle

Workflow: `/speckit-specify` → `/speckit-clarify` → `/speckit-plan` → `/speckit-tasks`
→ `/speckit-analyze` → `/speckit-implement`. Auto-commits on each phase via
`.specify/extensions.yml` git hooks.

<!-- SPECKIT START -->
Active plan: [specs/001-doc-intel-10k/plan.md](./specs/001-doc-intel-10k/plan.md)
Spec: [specs/001-doc-intel-10k/spec.md](./specs/001-doc-intel-10k/spec.md)
Constitution: [.specify/memory/constitution.md](./.specify/memory/constitution.md)
<!-- SPECKIT END -->
