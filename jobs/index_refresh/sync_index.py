"""Create-if-missing and sync the Vector Search index over gold_filing_sections_indexable.

Triggered by `resources/jobs/index_refresh.job.yml` whenever the source table updates.
The Delta-Sync index source view filters `WHERE embed_eligible = true`, so quality
filtering happens at ingest (constitution principle IV).
"""

from __future__ import annotations

import argparse
import logging
import sys

from databricks.sdk import WorkspaceClient
from databricks.vector_search.client import VectorSearchClient


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

    vsc = VectorSearchClient(disable_notice=True)
    indexes = {idx["name"] for idx in vsc.list_indexes(name=args.endpoint).get("vector_indexes", [])}

    if args.index not in indexes:
        log.info("creating Delta-Sync index %s", args.index)
        vsc.create_delta_sync_index_and_wait(
            endpoint_name=args.endpoint,
            index_name=args.index,
            source_table_name=args.source_table,
            primary_key=args.primary_key,
            pipeline_type="TRIGGERED",
            embedding_source_column="summary",
            embedding_model_endpoint_name=args.embedding_endpoint,
        )
        log.info("index created and initial sync complete")
        return 0

    log.info("index %s exists; triggering sync", args.index)
    vsc.get_index(endpoint_name=args.endpoint, index_name=args.index).sync()
    log.info("sync triggered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
