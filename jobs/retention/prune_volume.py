"""90-day retention for raw 10-K PDFs in the UC volume (SC-010, FR / spec assumptions)."""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys

from databricks.sdk import WorkspaceClient


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--catalog", required=True)
    p.add_argument("--schema", required=True)
    p.add_argument("--volume", required=True)
    p.add_argument("--days", type=int, default=90)
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("retention")
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.days)
    base = f"/Volumes/{args.catalog}/{args.schema}/{args.volume}"

    w = WorkspaceClient()
    removed = 0
    for entry in w.files.list_directory_contents(base):
        if entry.is_directory:
            continue
        mtime = dt.datetime.fromtimestamp(entry.modification_time / 1000, tz=dt.timezone.utc)
        if mtime < cutoff:
            log.info("removing %s (modified %s)", entry.path, mtime.isoformat())
            w.files.delete(entry.path)
            removed += 1

    log.info("retention pass complete: removed=%d cutoff=%s", removed, cutoff.isoformat())
    return 0


if __name__ == "__main__":
    sys.exit(main())
