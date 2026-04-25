"""Thin Lakebase Postgres client for the Streamlit App.

Persists conversation history, query logs, and feedback per the contracts in
`specs/001-doc-intel-10k/contracts/`. The Databricks App database resource
binding exposes standard Postgres env vars (PGHOST, PGPORT, PGUSER,
PGPASSWORD, PGDATABASE).

Databricks Apps + Lakebase docs (https://docs.databricks.com/aws/en/oltp/) —
initialize schema at
startup is the canonical pattern. Tables get owned by whatever Postgres user
is connected at first init. In deployed mode this is the App SP (because the
`database` resource binding maps PGUSER to the SP's client_id). In local-dev
mode this is whoever the developer is authenticated as. To avoid ownership
divergence, local-dev runs MUST authenticate as the same App SP — see
app/README.md "Running locally" for the env var contract.
"""

from __future__ import annotations

import logging
import os
import uuid
from contextlib import contextmanager
from typing import Iterator

import psycopg

_log = logging.getLogger(__name__)


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
    dsn = os.environ.get("DOCINTEL_LAKEBASE_DSN")
    if dsn:
        conninfo = dsn
        kwargs = {}
    else:
        required = ("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE")
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            raise RuntimeError(f"Lakebase binding missing Postgres env vars: {', '.join(missing)}")
        conninfo = ""
        kwargs = {
            "host": os.environ["PGHOST"],
            "port": os.environ["PGPORT"],
            "user": os.environ["PGUSER"],
            "password": os.environ["PGPASSWORD"],
            "dbname": os.environ["PGDATABASE"],
        }
    with psycopg.connect(conninfo, autocommit=True, **kwargs) as c:
        yield c


def init_schema() -> None:
    """Idempotent CREATE TABLE IF NOT EXISTS. Logs the connected role so
    deployed-vs-local identity divergence is debuggable from app logs.
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute("SELECT current_user")
        row = cur.fetchone()
        connected_user = row[0] if row else "<unknown>"
        expected_sp = os.environ.get("DATABRICKS_CLIENT_ID")
        if expected_sp and connected_user != expected_sp:
            _log.warning(
                "Lakebase init connected as %r; expected App SP %r. "
                "Tables created in this run will be owned by the wrong principal "
                "and may not be writable from the deployed App. See app/README.md.",
                connected_user, expected_sp,
            )
        else:
            _log.info("Lakebase init connected as %r", connected_user)
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
