"""Log the AnalystAgent as an MLflow pyfunc model, register it in UC, and assign the @<target> alias.

Invoked from the GitHub Actions deploy step. Idempotent — re-running creates a new
version and re-points the alias.
"""

from __future__ import annotations

import argparse
import os
import sys

import mlflow
from mlflow.models.auth_policy import AuthPolicy, SystemAuthPolicy, UserAuthPolicy
from mlflow.models.resources import (
    DatabricksServingEndpoint,
    DatabricksSQLWarehouse,
    DatabricksVectorSearchIndex,
)
from mlflow.models.signature import ModelSignature
from mlflow.types.schema import AnyType, ColSpec, Schema
from databricks.sdk import WorkspaceClient

from agent.analyst_agent import AnalystAgent


# Foundation + re-rank endpoints called by the agent (resolved here so the
# log_model auth_policy can enumerate them). Defaults match databricks.yml.
_FOUNDATION_ENDPOINT = os.environ.get("DOCINTEL_FOUNDATION_ENDPOINT", "databricks-meta-llama-3-3-70b-instruct")
_RERANK_ENDPOINT = os.environ.get("DOCINTEL_RERANK_ENDPOINT", "databricks-bge-rerank-v2")


def _auth_policy(catalog: str, schema: str, warehouse_id: str) -> AuthPolicy:
    """OBO-ready auth policy for the analyst pyfunc.

    System resources: enumerated so MLflow grants the deploying SP access at
    deploy time (Databricks Apps service-principal permissions are
    auto-granted by resource declaration — see
    https://docs.databricks.com/aws/en/dev-tools/databricks-apps/access-data).

    User scopes: documented agent-side scopes per Databricks Model Serving
    OBO docs (https://docs.databricks.com/aws/en/generative-ai/agent-framework/
    agent-authentication-model-serving) — `model-serving` for downstream
    serving-endpoint calls (foundation + rerank), `vector-search` for the VS
    index. App-side scopes (`serving.serving-endpoints`,
    `vectorsearch.vector-search-indexes`) are different — those are declared
    on the App resource, not here.
    """
    resources = [
        DatabricksServingEndpoint(endpoint_name=_FOUNDATION_ENDPOINT),
        DatabricksServingEndpoint(endpoint_name=_RERANK_ENDPOINT),
        DatabricksVectorSearchIndex(index_name=f"{catalog}.{schema}.filings_summary_idx"),
        DatabricksSQLWarehouse(warehouse_id=warehouse_id),
    ]
    return AuthPolicy(
        system_auth_policy=SystemAuthPolicy(resources=resources),
        user_auth_policy=UserAuthPolicy(api_scopes=[
            "model-serving",
            "vector-search",
        ]),
    )


def _signature() -> ModelSignature:
    inputs = Schema(
        [
            ColSpec("string", "question"),
            ColSpec("integer", "top_k"),
            ColSpec("string", "company_filter"),
            ColSpec("integer", "fiscal_year_filter"),
            ColSpec("string", "conversation_id"),
        ]
    )
    # UC requires both inputs and outputs in the signature. citations is an
    # array of dicts whose shape varies between analyst and supervisor paths,
    # so declare it as AnyType to avoid serving-time truncation of the nested
    # structure while still satisfying UC's "outputs declared" check.
    outputs = Schema(
        [
            ColSpec("string", "answer"),
            ColSpec("boolean", "grounded"),
            ColSpec("long", "latency_ms"),
            ColSpec("long", "retrieved_count"),
            ColSpec("string", "agent_path"),
            ColSpec("string", "conversation_id"),
            ColSpec("string", "turn_id"),
            ColSpec(AnyType(), "citations"),
        ]
    )
    return ModelSignature(inputs=inputs, outputs=outputs)


def _promote_serving_endpoint(endpoint_name: str, model_name: str, version: str) -> None:
    """Point an existing serving endpoint at the newly registered UC model version.

    DAB alias syntax has been unreliable for this endpoint, so CI registers the
    model and then updates the served entity explicitly. On first bring-up the
    endpoint may not yet exist (the initial deploy can't create it without a
    model version) — in that case skip silently and let the subsequent
    `bundle deploy` create it from serving.yml.
    """
    w = WorkspaceClient()
    try:
        endpoint = w.api_client.do("GET", f"/api/2.0/serving-endpoints/{endpoint_name}")
    except Exception as exc:
        msg = str(exc)
        if "does not exist" in msg or "RESOURCE_DOES_NOT_EXIST" in msg or "404" in msg:
            print(f"serving endpoint {endpoint_name!r} does not exist yet; skipping promote (will be created by next bundle deploy)")
            return
        raise
    config = endpoint.get("config", {})
    served_entities = config.get("served_entities") or config.get("served_models") or []
    if not served_entities:
        # Bootstrap edge case: the first `bundle deploy` created the endpoint shell
        # but the served-entity creation failed (model didn't exist yet). Skip the
        # in-place update — the subsequent `bundle deploy` reads serving.yml and
        # populates the served entity from scratch with the bootstrap version.
        print(f"serving endpoint {endpoint_name!r} has no served entities (likely UPDATE_FAILED on first deploy); skipping promote (next bundle deploy will populate from serving.yml)")
        return

    entity = dict(served_entities[0])
    entity.update({"entity_name": model_name, "entity_version": str(version)})
    body = {
        "served_entities": [entity],
        "traffic_config": config.get(
            "traffic_config",
            {"routes": [{"served_model_name": entity["name"], "traffic_percentage": 100}]},
        ),
    }
    w.api_client.do("PUT", f"/api/2.0/serving-endpoints/{endpoint_name}/config", body=body)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--target", required=True, choices=["dev", "prod"])
    p.add_argument("--serving-endpoint", help="Existing serving endpoint to update to the new model version")
    args = p.parse_args()

    catalog = os.environ["DOCINTEL_CATALOG"]
    schema = os.environ["DOCINTEL_SCHEMA"]
    warehouse_id = os.environ["DOCINTEL_WAREHOUSE_ID"]
    name = f"{catalog}.{schema}.analyst_agent"
    alias = args.target

    mlflow.set_registry_uri("databricks-uc")
    with mlflow.start_run(run_name=f"analyst-agent-{alias}") as run:
        info = mlflow.pyfunc.log_model(
            name="analyst_agent",
            python_model=AnalystAgent(),
            registered_model_name=name,
            signature=_signature(),
            code_paths=["agent"],
            pip_requirements=open("agent/requirements.txt").read().splitlines(),
            auth_policy=_auth_policy(catalog, schema, warehouse_id),
        )
        version = info.registered_model_version
        client = mlflow.tracking.MlflowClient(registry_uri="databricks-uc")
        client.set_registered_model_alias(name=name, alias=alias, version=version)
        if args.serving_endpoint:
            _promote_serving_endpoint(args.serving_endpoint, name, version)
        print(f"registered {name} version={version} alias=@{alias} run_id={run.info.run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
