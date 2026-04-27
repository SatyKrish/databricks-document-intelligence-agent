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


def test_extract_citations_from_structured_kpi_answer() -> None:
    payload = {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": (
                            "ACME Corporation's revenue in fiscal year 2024 was $94.20 billion.\n\n"
                            "This information was extracted from ACME's official 10-K filing "
                            "(ACME_10K_2024.pdf) with high confidence (99.97%)."
                        ),
                    }
                ],
            }
        ]
    }

    citations = extract_citations(payload)

    assert citations == [
        {
            "filename": "ACME_10K_2024.pdf",
            "section_label": "Structured KPI extract",
            "snippet": (
                "This information was extracted from ACME's official 10-K filing "
                "(ACME_10K_2024.pdf) with high confidence (99.97%)."
            ),
            "score": 0.9997,
        }
    ]


def test_normalise_agent_response_marks_structured_kpi_answer_grounded() -> None:
    response = normalise_agent_response({
        "output_text": (
            "Revenue was $94.20 billion, sourced from ACME_10K_2024.pdf "
            "with extraction confidence 99.97%."
        )
    })

    assert response["grounded"] is True
    assert response["retrieved_count"] == 1
    assert response["citations"][0]["filename"] == "ACME_10K_2024.pdf"
    assert response["citations"][0]["score"] == 0.9997


def test_extract_citations_keeps_unsupported_answer_ungrounded() -> None:
    payload = {
        "output_text": (
            "The corpus does not contain a grounded answer for this metric. "
            "No source in ACME_10K_2024.pdf supports the requested value."
        )
    }

    assert extract_citations(payload) == []


def test_extract_citations_prefers_knowledge_footnotes_over_kpi_fallback() -> None:
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
            },
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": (
                            "Revenue was $94.20 billion, sourced from ACME_10K_2024.pdf "
                            "with confidence 99.97%."
                        ),
                    }
                ],
            },
        ]
    }

    citations = extract_citations(payload)

    assert len(citations) == 1
    assert citations[0]["section_label"] == "Knowledge Assistant citation"


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
