# Security — Databricks Document Intelligence Agent

## Supported Security Posture

This reference is designed for Databricks workspaces using Unity Catalog, Agent Bricks, AI Gateway, Databricks Apps resource bindings, and end-to-end on-behalf-of (OBO) user identity. The App requires the request's `x-forwarded-access-token`; missing tokens fail loudly.

## Enabling End-To-End OBO

1. Workspace admin enables Databricks Apps user-token passthrough.
2. Redeploy and run the app resource. The deploy fails if the workspace cannot grant the declared `user_api_scopes`.
3. Verify `serving.serving-endpoints` and `sql` scopes are present after deployment.
4. Verify audit logs show downstream calls under the invoking user where required.

Agent Bricks / AI Gateway enforce downstream access to document Q&A, SQL tools, models, and any external tools under the invoking user's identity.

## Secrets And Credentials

- Do not commit Databricks tokens, service-principal secrets, Postgres passwords, or local app settings.
- `.claude/settings.local.json`, `.databricks/`, `.venv/`, MLflow local artifacts, Python caches, and local skill bundles are ignored.
- Use GitHub Actions secrets for `DATABRICKS_HOST` and `DATABRICKS_TOKEN`.
- Use Databricks resource bindings for app access to Lakebase and serving endpoints.

## Required Grants

Analyst groups need the full UC chain:

- `USE_CATALOG` on the catalog.
- `USE_SCHEMA`, `SELECT`, and `EXECUTE` on the schema.
- `READ_VOLUME` and `WRITE_VOLUME` on the raw filings volume when analysts upload PDFs.

## Reporting Issues

For security issues in a fork or deployment of this reference, contact the repository maintainer privately. Do not include secrets, workspace hostnames, tokens, or customer data in public issues.
