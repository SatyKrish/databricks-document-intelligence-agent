"""Log the AnalystAgent as an MLflow pyfunc model, register it in UC, and assign the @<target> alias.

Invoked from the GitHub Actions deploy step. Idempotent — re-running creates a new
version and re-points the alias.
"""

from __future__ import annotations

import argparse
import os
import sys

import mlflow
from mlflow.models.signature import ModelSignature
from mlflow.types.schema import ColSpec, Schema
from databricks.sdk import WorkspaceClient

from agent.analyst_agent import AnalystAgent


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
    # Do not declare outputs here. The pyfunc returns a rich JSON-like dict
    # (answer, grounded, citations, latency_ms, turn_id, etc.); an underspecified
    # output schema causes serving-time validation/truncation failures.
    return ModelSignature(inputs=inputs)


def _promote_serving_endpoint(endpoint_name: str, model_name: str, version: str) -> None:
    """Point an existing serving endpoint at the newly registered UC model version.

    DAB alias syntax has been unreliable for this endpoint, so CI registers the
    model and then updates the served entity explicitly.
    """
    w = WorkspaceClient()
    endpoint = w.api_client.do("GET", f"/api/2.0/serving-endpoints/{endpoint_name}")
    config = endpoint.get("config", {})
    served_entities = config.get("served_entities") or config.get("served_models") or []
    if not served_entities:
        raise RuntimeError(f"Serving endpoint {endpoint_name!r} has no served entities to update")

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
