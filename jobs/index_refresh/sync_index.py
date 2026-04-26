"""Create-if-missing and sync the Vector Search index over gold_filing_sections_indexable.

Triggered by `resources/jobs/index_refresh.job.yml` whenever the source table updates.
The Delta-Sync index source view filters `WHERE embed_eligible = true`, so quality
filtering happens at ingest (constitution principle IV).
"""

from __future__ import annotations

import argparse
from datetime import timedelta
import logging
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


def _sync_index_when_ready(w: WorkspaceClient, index_name: str, *, timeout_seconds: int = 1200) -> None:
    deadline = time.time() + timeout_seconds
    next_log = 60
    started = time.time()
    while True:
        try:
            w.vector_search_indexes.sync_index(index_name)
            return
        except Exception as exc:
            message = str(exc)
            transient = "not ready to sync yet" in message or "needs to be in one of the following states" in message
            if not transient or time.time() >= deadline:
                raise
            elapsed = int(time.time() - started)
            if elapsed >= next_log:
                log_message = message.splitlines()[0] if message else type(exc).__name__
                logging.getLogger("vs-sync").info(
                    "index %s is not syncable yet after %ss: %s",
                    index_name,
                    elapsed,
                    log_message,
                )
                next_log += 60
            time.sleep(15)


def main() -> None:
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
        return

    log.info("index %s exists; triggering sync", args.index)
    _wait_index_ready(w, args.index)
    _sync_index_when_ready(w, args.index)
    log.info("sync triggered")


if __name__ == "__main__":
    main()
