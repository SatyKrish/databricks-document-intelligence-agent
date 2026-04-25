# Security

## Supported Security Posture

This reference is designed for Databricks workspaces using Unity Catalog, service-principal deployment, Databricks Apps resource bindings, Model Serving auth policies, and optional end-to-end on-behalf-of (OBO) user identity.

## Identity Modes

| Mode | Use | Production row-level security |
|---|---|---|
| App SP fallback | Local development, reference demos, workspaces without Apps user-token passthrough | No |
| End-to-end OBO | Production analyst use | Yes, after audit verification |

SP fallback is intentionally supported so the reference can run in workspaces that do not yet expose Databricks Apps user-token passthrough. It is not sufficient for production deployments that promise user-specific UC row/column enforcement.

## Enabling End-To-End OBO

1. Workspace admin enables Databricks Apps user-token passthrough.
2. Uncomment `user_api_scopes` in `resources/consumers/analyst.app.yml`.
3. Redeploy and run the app resource.
4. Verify `serving.serving-endpoints` and `sql` scopes are present after deployment.
5. Verify audit logs show downstream calls under the invoking user where required.

The served agent also declares an MLflow auth policy in `agent/log_and_register.py` using Model Serving OBO scopes (`model-serving`, `vector-search`) and system resources.

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
