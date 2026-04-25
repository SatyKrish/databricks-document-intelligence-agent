"""Streamlit UI for the 10-K Analyst.

Chat over the indexed corpus. Renders citations as chips and a thumbs-up/down
feedback widget per FR-008. Persists turns and feedback to Lakebase via
`lakebase_client`.
"""

from __future__ import annotations

import os
import uuid

import streamlit as st
from databricks.sdk import WorkspaceClient
from databricks.sdk.config import Config

from app import lakebase_client


AGENT_ENDPOINT = os.environ["DOCINTEL_AGENT_ENDPOINT"]  # set via resource binding in resources/consumers/analyst.app.yml


@st.cache_resource
def _sp_client() -> WorkspaceClient:
    """Service-principal-scoped client for app-owned operations (Lakebase init, etc.)."""
    return WorkspaceClient()


@st.cache_resource(ttl=3600)
def _user_client(token: str | None) -> WorkspaceClient:
    """User-scoped (OBO) client built from the request's x-forwarded-access-token.

    Databricks Apps OBO docs:
    https://docs.databricks.com/aws/en/dev-tools/databricks-apps/iam-auth.
    Streamlit gotcha (per the Apps runtime docs): the OBO token is captured at
    the initial HTTP request, then the connection switches to WebSocket — the
    token never refreshes. Long-lived sessions should reload the page after
    permission changes.

    `token=None` → SP fallback (local dev, or unauthenticated requests).
    """
    if not token:
        return _sp_client()
    return WorkspaceClient(config=Config(
        host=os.environ["DATABRICKS_HOST"],
        token=token,
    ))


def _agent_client() -> WorkspaceClient:
    return _user_client(st.context.headers.get("x-forwarded-access-token"))


def _user_email() -> str:
    return st.context.headers.get("X-Forwarded-Email") or os.environ.get("DOCINTEL_USER_EMAIL", "anonymous@example.com")


def _query_agent(question: str, conversation_id: str) -> dict:
    try:
        out = _agent_client().serving_endpoints.query(
            name=AGENT_ENDPOINT,
            inputs=[{"question": question, "conversation_id": conversation_id, "top_k": 5}],
        )
        raw = out.predictions if hasattr(out, "predictions") else out["predictions"]
        return raw[0] if isinstance(raw, list) else raw
    except Exception as exc:
        return {
            "answer": "The analyst agent is unavailable right now. Please try again.",
            "grounded": False,
            "citations": [],
            "latency_ms": 0,
            "retrieved_count": 0,
            "agent_path": "app_error",
            "conversation_id": conversation_id,
            "turn_id": str(uuid.uuid4()),
            "error": str(exc),
        }


def _ensure_session() -> tuple[str, str]:
    if "conversation_id" not in st.session_state:
        st.session_state.conversation_id = str(uuid.uuid4())
        lakebase_client.init_schema()
        lakebase_client.ensure_conversation(uuid.UUID(st.session_state.conversation_id), _user_email())
    if "history" not in st.session_state:
        st.session_state.history = []
    return st.session_state.conversation_id, _user_email()


def _render_citations(citations: list[dict]) -> None:
    if not citations:
        st.caption("No citations — the agent did not find a grounded source.")
        return
    cols = st.columns(min(len(citations), 4))
    for i, c in enumerate(citations[:4]):
        with cols[i]:
            st.markdown(f"**`{c['filename']}`**\n\n{c['section_label']} — score {c['score']:.2f}")
            if c.get("snippet"):
                st.caption(c["snippet"])


def _render_feedback(turn_id: str, user_email: str) -> None:
    cols = st.columns([1, 1, 6])
    with cols[0]:
        if st.button("👍", key=f"up-{turn_id}"):
            lakebase_client.write_feedback(turn_id=turn_id, user_email=user_email, rating="up", comment=None)
            st.toast("Thanks for the feedback")
    with cols[1]:
        if st.button("👎", key=f"down-{turn_id}"):
            st.session_state[f"comment-{turn_id}"] = ""
    if f"comment-{turn_id}" in st.session_state:
        comment = st.text_input("Why?", key=f"comment-input-{turn_id}")
        if comment and st.button("Submit", key=f"submit-{turn_id}"):
            lakebase_client.write_feedback(
                turn_id=turn_id, user_email=user_email, rating="down", comment=comment
            )
            del st.session_state[f"comment-{turn_id}"]
            st.toast("Thanks — we'll dig in.")


def main() -> None:
    st.set_page_config(page_title="10-K Analyst", layout="wide")
    st.title("10-K Analyst")
    st.caption("Ask questions about indexed SEC 10-K filings. Answers come with citations.")

    conversation_id, user_email = _ensure_session()

    for turn in st.session_state.history:
        with st.chat_message("user"):
            st.write(turn["question"])
        with st.chat_message("assistant"):
            st.markdown(turn["response"]["answer"])
            _render_citations(turn["response"].get("citations", []))
            _render_feedback(turn["response"]["turn_id"], user_email)

    if question := st.chat_input("Ask about a filing or compare across companies"):
        with st.chat_message("user"):
            st.write(question)
        with st.chat_message("assistant"):
            with st.spinner("Retrieving and reasoning…"):
                response = _query_agent(question, conversation_id)
            st.markdown(response["answer"])
            _render_citations(response.get("citations", []))
            lakebase_client.log_turn(
                turn_id=response["turn_id"],
                conversation_id=uuid.UUID(conversation_id),
                response=response,
                question=question,
            )
            _render_feedback(response["turn_id"], user_email)
        st.session_state.history.append({"question": question, "response": response})


if __name__ == "__main__":
    main()
