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
    outputs = Schema([ColSpec("string", "answer"), ColSpec("string", "agent_path")])
    return ModelSignature(inputs=inputs, outputs=outputs)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--target", required=True, choices=["dev", "prod"])
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
        print(f"registered {name} version={version} alias=@{alias} run_id={run.info.run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
