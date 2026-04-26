# Security — Databricks Document Intelligence Agent

## Supported Security Posture

This reference is designed for Databricks workspaces using Unity Catalog, Agent Bricks, AI Gateway, Databricks Apps resource bindings, and mandatory end-to-end on-behalf-of (OBO) user identity.

## Identity Modes

| Mode | Use | Production row-level security |
|---|---|---|
| End-to-end OBO | Demo and production analyst use | Yes, after audit verification |

Service-principal fallback is not supported for the agent path. If Databricks Apps user-token passthrough, Agent Bricks OBO, or AI Gateway identity enforcement is unavailable, deployment must fail with an actionable prerequisite error.

## Enabling End-To-End OBO

1. Workspace admin enables Databricks Apps user-token passthrough.
2. Declare the required `user_api_scopes` in `resources/consumers/analyst.app.yml`.
3. Redeploy and run the app resource.
4. Verify `serving.serving-endpoints` and `sql` scopes are present after deployment.
5. Verify audit logs show downstream calls under the invoking user where required.

Agent Bricks / AI Gateway must enforce downstream access to document Q&A, SQL tools, models, and any external tools under the invoking user's identity. The previous custom MLflow auth-policy path has been removed from the production implementation.

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
