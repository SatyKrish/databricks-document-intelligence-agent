"""On-behalf-of credentials helpers for the analyst pyfunc.

Inside Model Serving, the model receives a per-request user context. The
canonical wiring (per Databricks Agent Framework / Model Serving OBO docs at
https://docs.databricks.com/aws/en/generative-ai/agent-framework/agent-authentication-model-serving)
is:

  - WorkspaceClient(credentials_strategy=ModelServingUserCredentials())
    (from databricks_ai_bridge — not databricks.sdk.credentials_provider)
  - VectorSearchClient(
        credential_strategy=CredentialStrategy.MODEL_SERVING_USER_CREDENTIALS,
    )

When the Model Serving deployment was logged WITHOUT a user auth_policy (or
the workspace lacks user-token-passthrough), `ModelServingUserCredentials()`
raises at instantiation. Callers fall back to default SP auth — which keeps
tests, local dev, and OBO-disabled workspaces working without code changes.

`agent/log_and_register.py` declares the matching `UserAuthPolicy` with the
documented agent-side scopes `model-serving` and `vector-search`, and the
SystemAuthPolicy with the resources the model touches; together they tell
Model Serving to inject the per-request user token into `predict()`.
"""

from __future__ import annotations

import logging

from databricks.sdk import WorkspaceClient
from databricks_ai_bridge import ModelServingUserCredentials

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
