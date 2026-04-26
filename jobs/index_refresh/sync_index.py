"""Create-if-missing and sync the Vector Search index over gold_filing_sections_indexable.

Triggered by `resources/jobs/index_refresh.job.yml` whenever the source table updates.
The Delta-Sync index source view filters `WHERE embed_eligible = true`, so quality
filtering happens at ingest (constitution principle IV).
"""

from __future__ import annotations

import argparse
from datetime import timedelta
import logging
import sys
import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.vectorsearch import (
    DeltaSyncVectorIndexSpecRequest,
    EmbeddingSourceColumn,
    PipelineType,
    VectorIndexType,
)


def _wait_index_ready(w: WorkspaceClient, index_name: str, *, timeout_seconds: int = 1200) -> None:
    deadline = time.time() + timeout_seconds
    while True:
        index = w.vector_search_indexes.get_index(index_name)
        status = index.status
        if status and status.ready:
            return
        if time.time() >= deadline:
            message = getattr(status, "message", None) or "UNKNOWN"
            raise TimeoutError(f"Vector Search index {index_name} not ready after {timeout_seconds}s: {message}")
        time.sleep(15)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--endpoint", required=True)
    p.add_argument("--index", required=True)
    p.add_argument("--source-table", required=True)
    p.add_argument("--embedding-endpoint", required=True)
    p.add_argument("--primary-key", default="section_uid")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("vs-sync")

    w = WorkspaceClient()
    w.vector_search_endpoints.wait_get_endpoint_vector_search_endpoint_online(
        args.endpoint,
        timeout=timedelta(minutes=20),
    )
    indexes = {idx.name for idx in w.vector_search_indexes.list_indexes(endpoint_name=args.endpoint)}

    if args.index not in indexes:
        log.info("creating Delta-Sync index %s", args.index)
        w.vector_search_indexes.create_index(
            name=args.index,
            endpoint_name=args.endpoint,
            primary_key=args.primary_key,
            index_type=VectorIndexType.DELTA_SYNC,
            delta_sync_index_spec=DeltaSyncVectorIndexSpecRequest(
                source_table=args.source_table,
                pipeline_type=PipelineType.TRIGGERED,
                embedding_source_columns=[
                    EmbeddingSourceColumn(
                        name="summary",
                        embedding_model_endpoint_name=args.embedding_endpoint,
                    )
                ],
            ),
        )
        _wait_index_ready(w, args.index)
        log.info("index created and initial sync complete")
        return 0

    log.info("index %s exists; triggering sync", args.index)
    w.vector_search_indexes.sync_index(args.index)
    log.info("sync triggered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
