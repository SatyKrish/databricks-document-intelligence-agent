# Streamlit App — runtime + local-dev guide

Source for the Databricks App `doc-intel-analyst-${target}`. Streamlit chat UI over the agent endpoint, with citation chips, thumbs feedback, and Lakebase persistence.

## Files

| File | Purpose |
|---|---|
| `app.py` | Streamlit entry point — chat loop, OBO client, citation rendering. |
| `app.yaml` | Databricks Apps runtime config (port, address, CORS/XSRF env vars). |
| `lakebase_client.py` | psycopg-based persistence to Lakebase Postgres. |
| `requirements.txt` | Python deps installed by the Apps runtime. |

## Running deployed (canonical)

```bash
databricks bundle deploy -t dev
databricks bundle run -t dev analyst_app
# Open the App URL from the workspace UI ("Apps" → doc-intel-analyst-dev)
```

The first request creates the `conversation_history`, `query_logs`, and `feedback` tables in Lakebase. Tables are owned by the App's bound service principal (auto-granted `CAN_CONNECT_AND_CREATE` per `resources/apps/analyst.app.yml`).

## Running locally

For iteration speed you may want to run the Streamlit app on your laptop against a deployed dev workspace. **Authenticate as the App's bound service principal** so Lakebase schema init produces the same ownership as the deployed App:

```bash
export DATABRICKS_HOST=https://<your-workspace>.cloud.databricks.com
export DATABRICKS_CLIENT_ID=<app-sp-application-id>
export DATABRICKS_CLIENT_SECRET=<app-sp-secret>

# Lakebase env vars (PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE) come from
# the App resource binding when deployed. Locally, derive them with:
eval "$(databricks apps get doc-intel-analyst-dev \
  --output json | jq -r '.resources[] | select(.name=="docintel-lakebase") | .database | @sh "
export PGHOST=\(.host) PGPORT=\(.port) PGUSER=\(.username) PGPASSWORD=\(.password) PGDATABASE=\(.database)"')"

export DOCINTEL_AGENT_ENDPOINT=analyst-agent-dev
streamlit run app/app.py
```

If you accidentally run with user creds (`DATABRICKS_CLIENT_ID`/`SECRET` unset), `lakebase_client.init_schema()` logs a warning identifying the mismatch — the tables get created under your user account, not the App SP, and the deployed App will lose write access. Drop the user-owned tables and re-init under the App SP to recover:

```sql
-- connected as the App SP via the local-dev env above
DROP TABLE IF EXISTS feedback CASCADE;
DROP TABLE IF EXISTS query_logs CASCADE;
DROP TABLE IF EXISTS conversation_history CASCADE;
-- next streamlit run will re-init under the App SP
```

## OBO (on-behalf-of) flow

The app forwards each user's `x-forwarded-access-token` header to the agent serving endpoint via a `WorkspaceClient(token=...)` cache (`app.py:_user_client`). Agent-side UC SQL calls then run as the user, not the App SP — UC ACLs are honored end-to-end.

`user_api_scopes` declared in `resources/apps/analyst.app.yml` (`sql`, `iam.access-control:read`, `iam.current-user:read`) — required for OBO to work for UC SQL queries inside the agent.

**Streamlit gotcha (skill `databricks-apps/references/other-frameworks.md` §8)**: the OBO token is captured at the initial HTTP request; the connection then upgrades to WebSocket and the token never refreshes. If a user's UC permissions change mid-session, ask them to reload the page.

**Local-dev caveat**: `st.context.headers` won't have `x-forwarded-access-token` when running `streamlit run` outside the Databricks Apps reverse proxy, so the OBO helper falls back to the SP client. That's fine for development — UC ACLs in dev workspaces are usually permissive — but verify against deployed dev before assuming OBO works.
