"""On-behalf-of credentials helpers for the analyst pyfunc.

Inside Model Serving, the model receives a per-request user context via
`ModelServingUserCredentials`. The strategy generates a short-lived token
scoped to the invoking user — Vector Search, SQL warehouse, and downstream
serving-endpoint calls then run as that user, so UC ACLs and row filters
are enforced.

When the Model Serving deployment has no auth_policy (or the workspace
hasn't enabled user-token-passthrough), `ModelServingUserCredentials()`
raises at instantiation. Callers fall back to default SP auth — which is
the correct steady-state for environments without OBO.

Skill: databricks-apps/references/platform-guide.md §Authentication.
Databricks Model Serving OBO docs: ModelServingUserCredentials() inside
predict, paired with an MLflow auth_policy at log_model time.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.credentials_provider import ModelServingUserCredentials

_log = logging.getLogger(__name__)


def user_workspace() -> WorkspaceClient:
    """User-scoped client when OBO is enabled; SP fallback otherwise.

    The fallback path keeps tests, local dev, and OBO-disabled workspaces
    working without code changes.
    """
    try:
        return WorkspaceClient(credentials_strategy=ModelServingUserCredentials())
    except Exception as exc:  # OBO disabled, outside Serving runtime, etc.
        _log.debug("ModelServingUserCredentials unavailable (%s); falling back to default auth", exc)
        return WorkspaceClient()


def user_vector_search_kwargs(ws: WorkspaceClient) -> dict[str, Any]:
    """Build VectorSearchClient kwargs that ride the user's identity.

    VS doesn't accept a databricks-sdk credentials_strategy directly; it
    wants `workspace_url` + `personal_access_token`. We resolve a token from
    the user-scoped WorkspaceClient's config (which may itself be the SP
    when OBO is disabled — same flow, just SP-scoped). `disable_notice=True`
    suppresses the deprecation warning Databricks adds to every client init.
    """
    cfg = ws.config
    headers = cfg.authenticate() or {}
    auth_header = headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else None
    return {
        "workspace_url": cfg.host or os.environ.get("DATABRICKS_HOST"),
        "personal_access_token": token,
        "disable_notice": True,
    }
