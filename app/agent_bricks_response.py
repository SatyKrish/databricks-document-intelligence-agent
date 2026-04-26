"""Normalize Agent Bricks serving endpoint responses for app and eval paths."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any


APP_EMPTY_TEXT = "The Agent Bricks endpoint returned a response without displayable text."


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

    output = payload.get("output")
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            if not isinstance(item, Mapping):
                continue
            content_items = item.get("content", [])
            if not isinstance(content_items, list):
                continue
            for content in content_items:
                text = content.get("text") if isinstance(content, Mapping) else None
                if isinstance(text, str):
                    parts.append(text)
        if parts:
            return "\n".join(parts)

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
