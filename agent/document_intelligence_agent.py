"""Document Intelligence Agent definition and deployment logic."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from typing import Iterable, TypeVar

from databricks.sdk import WorkspaceClient
from databricks.sdk.common.types.fieldmask import FieldMask
from databricks.sdk.service.iam import AccessControlRequest, PermissionLevel
from databricks.sdk.service.knowledgeassistants import (
    IndexSpec,
    KnowledgeAssistant,
    KnowledgeSource,
)
from databricks.sdk.service.supervisoragents import (
    KnowledgeAssistant as SupervisorKnowledgeAssistant,
)
from databricks.sdk.service.supervisoragents import SupervisorAgent, Tool, UcFunction


T = TypeVar("T")


@dataclass
class DocumentIntelligenceAgentRuntime:
    knowledge_assistant: KnowledgeAssistant
    supervisor_agent: SupervisorAgent
    kpi_function: str
    supervisor_endpoint: str
    knowledge_endpoint: str

    def as_dict(self) -> dict:
        return {
            "knowledge_assistant": _as_dict(self.knowledge_assistant),
            "supervisor_agent": _as_dict(self.supervisor_agent),
            "kpi_function": self.kpi_function,
            "supervisor_endpoint": self.supervisor_endpoint,
            "knowledge_endpoint": self.knowledge_endpoint,
        }


def _find_by_display_name(items: Iterable[T], display_name: str) -> T | None:
    for item in items:
        if getattr(item, "display_name", None) == display_name:
            return item
    return None


def _id_from_name(name: str | None) -> str:
    if not name:
        raise ValueError("Agent Bricks resource did not return a name")
    return name.rsplit("/", 1)[-1]


def _as_dict(obj: object) -> dict:
    if hasattr(obj, "as_dict"):
        return obj.as_dict()
    if hasattr(obj, "__dataclass_fields__"):
        return asdict(obj)
    return {"value": str(obj)}


def _enum_name(value: object) -> str:
    if value is None:
        return ""
    raw = getattr(value, "value", value)
    return str(raw).upper().rsplit(".", 1)[-1]


def _statement_error(status: object) -> str:
    error = getattr(status, "error", None)
    if error is None:
        return str(status)
    message = getattr(error, "message", None)
    error_code = getattr(error, "error_code", None)
    if message and error_code:
        return f"{error_code}: {message}"
    return str(error)


def _wait_statement_succeeded(
    w: WorkspaceClient,
    result: object,
    *,
    label: str,
    timeout_seconds: int = 300,
) -> None:
    started = time.time()
    deadline = started + timeout_seconds
    next_log = 60
    failed_states = {"FAILED", "CANCELED", "CANCELLED", "CLOSED"}

    while True:
        status = getattr(result, "status", None)
        state = _enum_name(getattr(status, "state", None))
        if state == "SUCCEEDED":
            return
        if state in failed_states:
            raise RuntimeError(f"{label} failed: {_statement_error(status)}")

        statement_id = getattr(result, "statement_id", None)
        if not statement_id:
            if state:
                raise RuntimeError(f"{label} did not finish and returned no statement_id (state={state})")
            return

        elapsed = int(time.time() - started)
        if time.time() >= deadline:
            raise TimeoutError(f"{label} did not finish within {timeout_seconds}s (last state={state or 'UNKNOWN'})")
        if elapsed >= next_log:
            print(f"still waiting on {label} after {elapsed}s (state={state or 'UNKNOWN'})", file=sys.stderr)
            next_log += 60
        time.sleep(5)
        result = w.statement_execution.get_statement(statement_id)


def _create_or_update_kpi_function(
    w: WorkspaceClient,
    *,
    catalog: str,
    schema: str,
    warehouse_id: str,
) -> str:
    function_name = f"{catalog}.{schema}.lookup_10k_kpis"
    statement = f"""
CREATE OR REPLACE FUNCTION {function_name}(company STRING)
RETURNS STRING
LANGUAGE SQL
COMMENT 'Return the newest governed 10-K KPI row for a company as JSON. Used by Agent Bricks Supervisor Agent.'
RETURN (
  SELECT to_json(named_struct(
    'filename', filename,
    'company_name', company_name,
    'fiscal_year', fiscal_year,
    'revenue', revenue,
    'ebitda', ebitda,
    'segment_revenue', segment_revenue,
    'top_risks', top_risks,
    'extraction_confidence', extraction_confidence
  ))
  FROM {catalog}.{schema}.gold_filing_kpis
  WHERE lower(company_name) LIKE concat('%', lower(company), '%')
     OR lower(filename) LIKE concat('%', lower(company), '%')
  ORDER BY fiscal_year DESC
  LIMIT 1
)
"""
    result = w.statement_execution.execute_statement(
        warehouse_id=warehouse_id,
        statement=statement,
        wait_timeout="50s",
    )
    _wait_statement_succeeded(
        w,
        result,
        label=f"CREATE OR REPLACE FUNCTION {function_name}",
        timeout_seconds=300,
    )
    return function_name


def _ensure_knowledge_assistant(
    w: WorkspaceClient,
    *,
    display_name: str,
    index_name: str,
) -> KnowledgeAssistant:
    description = (
        "Cited document Q&A over curated 10-K sections produced by the "
        "Document Intelligence pipeline."
    )
    instructions = (
        "Answer questions only from the provided 10-K knowledge source. "
        "Prefer exact figures and section-level citations. If the answer is "
        "not grounded in the indexed corpus, say that the corpus does not "
        "contain a grounded answer."
    )

    existing = _find_by_display_name(w.knowledge_assistants.list_knowledge_assistants(), display_name)
    desired = KnowledgeAssistant(
        display_name=display_name,
        description=description,
        instructions=instructions,
    )
    if existing is None:
        assistant = w.knowledge_assistants.create_knowledge_assistant(knowledge_assistant=desired)
    else:
        assistant = w.knowledge_assistants.update_knowledge_assistant(
            name=existing.name,
            knowledge_assistant=desired,
            update_mask=FieldMask(["description", "instructions"]),
        )

    source_display = "curated_10k_sections"
    source_description = (
        "Quality-filtered 10-K section summaries with filename and section "
        "metadata from the governed Document Intelligence Gold layer."
    )
    source = KnowledgeSource(
        display_name=source_display,
        description=source_description,
        source_type="index",
        index=IndexSpec(
            index_name=index_name,
            text_col="summary",
            doc_uri_col="filename",
        ),
    )

    sources = list(w.knowledge_assistants.list_knowledge_sources(parent=assistant.name))
    existing_source = next(
        (
            s
            for s in sources
            if s.display_name == source_display
            or (s.index is not None and s.index.index_name == index_name)
        ),
        None,
    )
    if existing_source is None:
        w.knowledge_assistants.create_knowledge_source(parent=assistant.name, knowledge_source=source)
    else:
        w.knowledge_assistants.update_knowledge_source(
            name=existing_source.name,
            knowledge_source=KnowledgeSource(
                display_name=source_display,
                description=source_description,
                source_type="index",
            ),
            update_mask=FieldMask(["display_name", "description"]),
        )

    w.knowledge_assistants.sync_knowledge_sources(name=assistant.name)
    return assistant


def _ensure_supervisor(
    w: WorkspaceClient,
    *,
    display_name: str,
    knowledge_assistant: KnowledgeAssistant,
    kpi_function_name: str,
) -> SupervisorAgent:
    description = (
        "Governed 10-K document intelligence supervisor for cited filing Q&A "
        "and structured KPI comparisons."
    )
    instructions = (
        "Use the Knowledge Assistant for narrative or section-level questions. "
        "Use the Unity Catalog KPI function for structured financial metrics "
        "and cross-company comparisons. Do not invent figures; cite the filing "
        "source or state that the corpus does not contain the answer."
    )
    desired = SupervisorAgent(
        display_name=display_name,
        description=description,
        instructions=instructions,
    )
    existing = _find_by_display_name(w.supervisor_agents.list_supervisor_agents(), display_name)
    if existing is None:
        supervisor = w.supervisor_agents.create_supervisor_agent(supervisor_agent=desired)
    else:
        supervisor = w.supervisor_agents.update_supervisor_agent(
            name=existing.name,
            supervisor_agent=desired,
            update_mask=FieldMask(["description", "instructions"]),
        )

    ka_tool = Tool(
        tool_type="knowledge_assistant",
        description=(
            "Answer cited questions about individual 10-K filings, risk "
            "factors, MD&A, notes, and narrative disclosures."
        ),
        knowledge_assistant=SupervisorKnowledgeAssistant(
            knowledge_assistant_id=knowledge_assistant.id or _id_from_name(knowledge_assistant.name),
        ),
    )
    kpi_tool = Tool(
        tool_type="uc_function",
        description=(
            "Fetch deterministic structured KPIs from the governed Gold table "
            "for a requested company."
        ),
        uc_function=UcFunction(name=kpi_function_name),
    )

    existing_tools = {
        (getattr(t, "tool_id", None) or _id_from_name(t.name)): t
        for t in w.supervisor_agents.list_tools(parent=supervisor.name)
    }
    for tool_id, tool in {
        "filings_knowledge_assistant": ka_tool,
        "structured_kpi_lookup": kpi_tool,
    }.items():
        if tool_id in existing_tools:
            w.supervisor_agents.update_tool(
                name=existing_tools[tool_id].name,
                tool=Tool(tool_type=tool.tool_type, description=tool.description),
                update_mask=FieldMask(["description"]),
            )
        else:
            w.supervisor_agents.create_tool(parent=supervisor.name, tool=tool, tool_id=tool_id)

    return supervisor


def _endpoint_status(endpoint: object) -> tuple[str, str]:
    state = getattr(endpoint, "state", None)
    if isinstance(state, dict):
        ready = state.get("ready")
        config_update = state.get("config_update")
    else:
        ready = getattr(state, "ready", None)
        config_update = getattr(state, "config_update", None)
    return _enum_name(ready), _enum_name(config_update)


def _wait_endpoint_ready(w: WorkspaceClient, endpoint_name: str, *, timeout_seconds: int = 600) -> object:
    started = time.time()
    deadline = started + timeout_seconds
    next_log = 60
    last_status = "not listable yet"

    while True:
        try:
            endpoint = w.serving_endpoints.get(endpoint_name)
            ready, config_update = _endpoint_status(endpoint)
            last_status = f"ready={ready or 'UNKNOWN'}, config_update={config_update or 'UNKNOWN'}"
            if config_update == "UPDATE_FAILED":
                raise RuntimeError(f"Agent Bricks endpoint {endpoint_name} update failed ({last_status})")
            if ready == "READY" and config_update in {"", "NOT_UPDATING"}:
                return endpoint
        except RuntimeError:
            raise
        except Exception as exc:
            last_status = f"not listable yet: {exc}"

        elapsed = int(time.time() - started)
        if time.time() >= deadline:
            raise RuntimeError(
                f"Agent Bricks endpoint {endpoint_name} was not ready within "
                f"{timeout_seconds}s ({last_status})"
            )
        if elapsed >= next_log:
            print(f"still waiting on endpoint {endpoint_name} after {elapsed}s ({last_status})", file=sys.stderr)
            next_log += 60
        time.sleep(15)


def _grant_endpoint_query(w: WorkspaceClient, endpoint_name: str, group_name: str) -> None:
    endpoint = _wait_endpoint_ready(w, endpoint_name)
    endpoint_id = getattr(endpoint, "id", None) or endpoint_name

    w.permissions.update(
        "serving-endpoints",
        endpoint_id,
        access_control_list=[
            AccessControlRequest(
                group_name=group_name,
                permission_level=PermissionLevel.CAN_QUERY,
            )
        ],
    )


def deploy_document_intelligence_agent(
    w: WorkspaceClient,
    *,
    target: str,
    catalog: str,
    schema: str,
    warehouse_id: str,
    analyst_group: str,
) -> DocumentIntelligenceAgentRuntime:
    index_name = f"{catalog}.{schema}.filings_summary_idx"

    kpi_function_name = _create_or_update_kpi_function(
        w,
        catalog=catalog,
        schema=schema,
        warehouse_id=warehouse_id,
    )
    knowledge_assistant = _ensure_knowledge_assistant(
        w,
        display_name=f"doc-intel-knowledge-{target}",
        index_name=index_name,
    )
    supervisor = _ensure_supervisor(
        w,
        display_name=f"doc-intel-supervisor-{target}",
        knowledge_assistant=knowledge_assistant,
        kpi_function_name=kpi_function_name,
    )

    if not supervisor.endpoint_name:
        raise RuntimeError(f"Supervisor Agent doc-intel-supervisor-{target} did not return an endpoint_name")
    if not knowledge_assistant.endpoint_name:
        raise RuntimeError(f"Knowledge Assistant doc-intel-knowledge-{target} did not return an endpoint_name")

    actual_supervisor_endpoint = supervisor.endpoint_name
    actual_knowledge_endpoint = knowledge_assistant.endpoint_name

    _grant_endpoint_query(w, actual_supervisor_endpoint, analyst_group)
    if actual_knowledge_endpoint:
        _grant_endpoint_query(w, actual_knowledge_endpoint, analyst_group)

    return DocumentIntelligenceAgentRuntime(
        knowledge_assistant=knowledge_assistant,
        supervisor_agent=supervisor,
        kpi_function=kpi_function_name,
        supervisor_endpoint=actual_supervisor_endpoint,
        knowledge_endpoint=actual_knowledge_endpoint,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default=os.environ.get("DOCINTEL_TARGET", "demo"))
    parser.add_argument("--catalog", default=os.environ.get("DOCINTEL_CATALOG"))
    parser.add_argument("--schema", default=os.environ.get("DOCINTEL_SCHEMA"))
    parser.add_argument("--warehouse-id", default=os.environ.get("DOCINTEL_WAREHOUSE_ID"))
    parser.add_argument("--analyst-group", default=os.environ.get("DOCINTEL_ANALYST_GROUP", "account users"))
    args = parser.parse_args()

    if not args.catalog or not args.schema or not args.warehouse_id:
        parser.error("--catalog, --schema, and --warehouse-id are required")

    runtime = deploy_document_intelligence_agent(
        WorkspaceClient(),
        target=args.target,
        catalog=args.catalog,
        schema=args.schema,
        warehouse_id=args.warehouse_id,
        analyst_group=args.analyst_group,
    )
    print(json.dumps(runtime.as_dict(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
