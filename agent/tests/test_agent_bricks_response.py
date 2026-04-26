from __future__ import annotations

from app.agent_bricks_response import extract_citations, extract_text, normalise_agent_response


def test_extract_text_from_responses_output_shape() -> None:
    payload = {
        "output": [
            {
                "content": [
                    {"text": "Revenue increased."},
                    {"text": "Risks were disclosed."},
                ]
            }
        ]
    }

    assert extract_text(payload) == "Revenue increased.\nRisks were disclosed."


def test_extract_text_from_chat_choices_shape() -> None:
    payload = {"choices": [{"message": {"content": "Choice response"}}]}

    assert extract_text(payload) == "Choice response"


def test_extract_text_prefers_final_agent_bricks_message() -> None:
    payload = {
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "Thinking"}]},
            {"type": "message", "content": [{"type": "output_text", "text": "Final answer"}]},
        ]
    }

    assert extract_text(payload) == "Final answer"


def test_extract_citations_from_agent_bricks_footnotes() -> None:
    payload = {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "[^p1]: Revenue was $94.2B. _ACME_10K_2024.pdf_",
                    }
                ],
            }
        ]
    }

    citations = extract_citations(payload)

    assert citations[0]["filename"] == "ACME_10K_2024.pdf"
    assert "Revenue was $94.2B" in citations[0]["snippet"]


def test_extract_citations_returns_empty_without_structured_sources_or_footnotes() -> None:
    payload = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "Final answer without footnotes."}],
            }
        ]
    }

    assert extract_citations(payload) == []


def test_normalise_agent_response_coerces_citations_and_latency() -> None:
    response = normalise_agent_response(
        {
            "output_text": "Grounded answer",
            "sources": [{"doc_uri": "filing.pdf"}, "legacy-source"],
            "latency_ms": "41",
        },
        conversation_id="conversation-1",
    )

    assert response["answer"] == "Grounded answer"
    assert response["grounded"] is True
    assert response["retrieved_count"] == 2
    assert response["citations"] == [{"doc_uri": "filing.pdf"}, {"source": "legacy-source"}]
    assert response["latency_ms"] == 41
    assert response["conversation_id"] == "conversation-1"
    assert response["agent_path"] == "agent_bricks_supervisor"
