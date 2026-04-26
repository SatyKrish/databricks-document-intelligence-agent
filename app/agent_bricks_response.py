"""Normalize Agent Bricks serving endpoint responses for app and eval paths."""

from __future__ import annotations

import uuid
import re
from collections.abc import Mapping
from typing import Any


# Observed during 2026-04-26 demo deploy validation: Knowledge Assistant
# citations appear as markdown footnotes in intermediate Agent Bricks messages,
# e.g. `[^p1]: ... _ACME_10K_2024.pdf_`. This is not a public structured
# citation contract. If citation chips stop showing filenames, grep live payloads
# for `[^` and `.pdf_`; extraction falls back to filename="source" for footnotes
# without a parseable filename and [] when no footnotes are present.
APP_EMPTY_TEXT = "The Agent Bricks endpoint returned a response without displayable text."
FILENAME_RE = re.compile(r"_([A-Za-z0-9][A-Za-z0-9_.-]*\.pdf)_")


def _output_text_groups(payload: Mapping[str, Any]) -> list[str]:
    output = payload.get("output")
    if not isinstance(output, list):
        return []

    groups: list[str] = []
    for item in output:
        if not isinstance(item, Mapping):
            continue
        content_items = item.get("content", [])
        if not isinstance(content_items, list):
            continue
        parts: list[str] = []
        for content in content_items:
            text = content.get("text") if isinstance(content, Mapping) else None
            if isinstance(text, str):
                parts.append(text)
        if parts:
            groups.append("\n".join(parts))
    return groups


def extract_text(payload: Mapping[str, Any], *, empty_text: str = "") -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    if isinstance(payload.get("response"), str):
        return payload["response"]

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        message = first.get("message", {}) if isinstance(first, Mapping) else {}
        content = message.get("content") if isinstance(message, Mapping) else None
        if isinstance(content, str):
            return content

    if isinstance(payload.get("output"), str):
        return payload["output"]
    output_groups = _output_text_groups(payload)
    if output_groups:
        return output_groups[-1]

    return empty_text


def extract_citations(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    citations = payload.get("citations") or payload.get("sources") or []
    if not isinstance(citations, list):
        return []
    normalized: list[dict[str, Any]] = []
    for citation in citations:
        if isinstance(citation, Mapping):
            normalized.append(dict(citation))
        elif citation is not None:
            normalized.append({"source": str(citation)})
    if normalized:
        return normalized

    # Walk all output groups, not just the final answer. The Supervisor's final
    # message can omit citations that the Knowledge Assistant returned earlier.
    for text in _output_text_groups(payload):
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("[^") or "]:" not in stripped:
                continue
            snippet = stripped.split("]:", 1)[1].strip()
            filename = ""
            match = FILENAME_RE.search(snippet)
            if match:
                filename = match.group(1)
            normalized.append({
                "filename": filename or "source",
                "section_label": "Knowledge Assistant citation",
                "snippet": snippet,
            })
    return normalized


def normalise_agent_response(
    payload: Mapping[str, Any],
    *,
    conversation_id: str | None = None,
    agent_path: str = "agent_bricks_supervisor",
    empty_text: str = APP_EMPTY_TEXT,
) -> dict[str, Any]:
    citations = extract_citations(payload)
    try:
        latency_ms = int(payload.get("latency_ms") or 0)
    except (TypeError, ValueError):
        latency_ms = 0

    response = {
        "answer": extract_text(payload, empty_text=empty_text),
        "grounded": bool(citations),
        "citations": citations,
        "latency_ms": latency_ms,
        "retrieved_count": len(citations),
        "agent_path": agent_path,
        "turn_id": str(uuid.uuid4()),
    }
    if conversation_id is not None:
        response["conversation_id"] = conversation_id
    return response
