"""Invoke Agent Bricks serving endpoints through the OpenAI-compatible path."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

from databricks.sdk import WorkspaceClient


def invoke_agent_endpoint(
    client: WorkspaceClient,
    endpoint_name: str,
    question: str,
    *,
    client_request_id: str | None = None,
    max_retries: int = 3,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    host = client.config.host.rstrip("/")
    url = f"{host}/serving-endpoints/{endpoint_name}/invocations"
    body = json.dumps({"input": [{"role": "user", "content": question}]}).encode("utf-8")
    # For an OBO WorkspaceClient built with Config(token=<x-forwarded-access-token>),
    # authenticate() emits that user token. There is no App SP fallback here.
    headers = {
        "Content-Type": "application/json",
        "X-Request-ID": client_request_id or str(uuid.uuid4()),
        **client.config.authenticate(),
    }

    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace")
            if raw.strip():
                return json.loads(raw)
            last_error = RuntimeError("empty response body")
        except json.JSONDecodeError as exc:
            last_error = RuntimeError(f"non-JSON response body: {exc}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code not in {429, 500, 502, 503, 504}:
                raise RuntimeError(f"Agent Bricks endpoint {endpoint_name} returned HTTP {exc.code}: {detail}") from exc
            last_error = RuntimeError(f"retryable HTTP {exc.code}: {detail}")

        if attempt < max_retries:
            time.sleep(2 * attempt)

    raise RuntimeError(f"Agent Bricks endpoint {endpoint_name} returned no usable response after {max_retries} attempts") from last_error
