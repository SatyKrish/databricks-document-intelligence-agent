"""Thin Lakebase Postgres client for the Streamlit App.

Persists conversation history, query logs, and feedback per the contracts in
`specs/001-doc-intel-10k/contracts/`. The connection DSN is injected by the
Databricks App runtime via env var `DOCINTEL_LAKEBASE_DSN`.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from typing import Iterator

import psycopg


_DSN = os.environ.get("DOCINTEL_LAKEBASE_DSN")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_history (
  conversation_id UUID PRIMARY KEY,
  user_email TEXT NOT NULL,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_turn_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS query_logs (
  turn_id UUID PRIMARY KEY,
  conversation_id UUID REFERENCES conversation_history(conversation_id),
  question TEXT NOT NULL,
  answer TEXT NOT NULL,
  citations JSONB NOT NULL,
  latency_ms INT NOT NULL,
  agent_path TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS feedback (
  feedback_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  turn_id UUID REFERENCES query_logs(turn_id),
  user_email TEXT NOT NULL,
  rating TEXT NOT NULL CHECK (rating IN ('up','down')),
  comment TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


@contextmanager
def _conn() -> Iterator[psycopg.Connection]:
    if not _DSN:
        raise RuntimeError("DOCINTEL_LAKEBASE_DSN not set; configure in app.yaml env")
    with psycopg.connect(_DSN, autocommit=True) as c:
        yield c


def init_schema() -> None:
    with _conn() as c, c.cursor() as cur:
        cur.execute(_SCHEMA)


def ensure_conversation(conversation_id: uuid.UUID, user_email: str) -> None:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO conversation_history (conversation_id, user_email) VALUES (%s, %s) "
            "ON CONFLICT (conversation_id) DO UPDATE SET last_turn_at = now()",
            (conversation_id, user_email),
        )


def log_turn(*, turn_id: str, conversation_id: uuid.UUID, response: dict, question: str) -> None:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO query_logs (turn_id, conversation_id, question, answer, citations, latency_ms, agent_path) "
            "VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)",
            (
                turn_id,
                conversation_id,
                question,
                response["answer"],
                psycopg.types.json.Json(response.get("citations", [])),
                response.get("latency_ms", 0),
                response.get("agent_path", "analyst"),
            ),
        )


def write_feedback(*, turn_id: str, user_email: str, rating: str, comment: str | None) -> None:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO feedback (turn_id, user_email, rating, comment) VALUES (%s, %s, %s, %s)",
            (turn_id, user_email, rating, comment),
        )
