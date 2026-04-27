# Security — Databricks Document Intelligence Agent

## Supported Security Posture

This reference is designed for Databricks workspaces using Unity Catalog, Agent Bricks, AI Gateway, Databricks Apps resource bindings, and end-to-end on-behalf-of (OBO) user identity in prod. Demo can run with `app_obo_required=false` when the workspace does not have Databricks Apps user-token passthrough enabled. In that mode the App service principal invokes Agent Bricks and is granted `CAN_QUERY` after deploy.

## Enabling End-To-End OBO

1. Set `app_obo_required=true` for the target. Prod does this by default.
2. Workspace admin enables Databricks Apps user-token passthrough.
3. Redeploy and run the app resource. The deploy fails if the workspace cannot grant the declared `user_api_scopes`.
4. Verify `serving.serving-endpoints` and `sql` scopes are present after deployment.
5. Verify audit logs show downstream calls under the invoking user where required.

With OBO enabled, Agent Bricks / AI Gateway enforce downstream access to document Q&A, SQL tools, models, and any external tools under the invoking user's identity.

## Secrets And Credentials

- Do not commit Databricks tokens, service-principal secrets, Postgres passwords, or local app settings.
- `.claude/settings.local.json`, `.databricks/`, `.venv/`, MLflow local artifacts, Python caches, and local skill bundles are ignored.
- Use GitHub Actions secrets for `DATABRICKS_HOST` and `DATABRICKS_TOKEN`.
- Use Databricks resource bindings for Lakebase. Agent Bricks endpoint access is granted directly to users or the App service principal, depending on target auth mode.

## Required Grants

Analyst groups need the full UC chain:

- `USE_CATALOG` on the catalog.
- `USE_SCHEMA`, `SELECT`, and `EXECUTE` on the schema.
- `READ_VOLUME` and `WRITE_VOLUME` on the raw filings volume when analysts upload PDFs.

## Reporting Issues

For security issues in a fork or deployment of this reference, contact the repository maintainer privately. Do not include secrets, workspace hostnames, tokens, or customer data in public issues.
