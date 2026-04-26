# Production Readiness — Databricks Document Intelligence Agent

This project is open-sourced as a Databricks reference implementation. Treat it as production-ready only after the evidence below is collected in the target workspace.

## Readiness Levels

| Level | Bar | Evidence |
|---|---|---|
| Reference-ready | Synthetic corpus demonstrates the full architecture | Demo bundle validates, staged bootstrap succeeds, synthetic CLEARS passes |
| Pilot-ready | Real filings exercise document variability and cost/latency | Reference-ready plus a reviewed EDGAR pilot corpus |
| Production-ready | Analysts can use it under governed identity and SLOs | Pilot-ready plus end-to-end OBO, dashboards, alerts, rollback, and runbook evidence |

## Reference-Ready Checklist

- `databricks bundle validate --strict -t demo` passes.
- `./scripts/bootstrap-demo.sh` succeeds in a clean demo workspace.
- Synthetic PDFs in `samples/` produce at least ACME/BETA/GAMMA KPI rows.
- Vector Search index sync completes and the Agent Bricks Supervisor endpoint answers a smoke question with citations.
- `python evals/clears_eval.py --endpoint analyst-agent-demo --dataset evals/dataset.jsonl` passes.
- App starts via `databricks bundle run -t demo analyst_app`.

## Pilot-Ready Checklist

- At least 5 representative public SEC 10-K filings are uploaded from EDGAR and processed.
- Section explosion produces meaningful section labels, not only `full_document` normalized rows.
- KPI extraction is manually reviewed for revenue, EBITDA, segment revenue, and top risks.
- Quality rubric distribution is reviewed; low-quality filings are retained in Gold but excluded from `gold_filing_sections_indexable`.
- Latency p95 is measured for single-filing and cross-company prompts.
- Estimated AI Functions, Vector Search, Agent Bricks, AI Gateway, Lakebase, and Apps costs are documented.

## Production-Ready Checklist

- Databricks Apps user-token passthrough is enabled in the workspace.
- `resources/consumers/analyst.app.yml:user_api_scopes` is declared and survives `bundle run`.
- Audit logs prove app requests, Agent Bricks, Knowledge Assistant, Vector Search, and structured KPI SQL calls execute under the invoking user where required.
- Service principal `run_as` is configured for prod via `--var service_principal_id=<sp-app-id>`.
- Analyst group grants include `USE_CATALOG`, `USE_SCHEMA`, `SELECT`, `EXECUTE`, `READ_VOLUME`, and `WRITE_VOLUME` as appropriate.
- CLEARS passes against the pilot corpus and synthetic regression corpus.
- Rollback is tested by reverting Agent Bricks configuration and redeploying the previous known-good bundle.
- Dashboards and monitors are deployed and reviewed by an owner.
- Alerting exists for pipeline failures, index-refresh failures, endpoint errors, app startup failures, CLEARS failures, and Lakebase write failures.

## Non-Goals For The Reference

- It is not a managed product.
- It does not include a legal/compliance review for SEC filing usage.
- It does not guarantee support for every 10-K layout or scanned PDF quality.
- It does not permit broad service-principal reads for production document Q&A.
