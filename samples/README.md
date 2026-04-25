# Sample 10-K filings

For local smoke-tests and the evaluation set, drop public SEC 10-K PDFs in this
directory and reference the filename from `evals/dataset.jsonl`. Filenames in
the eval set assume the canonical `<TICKER>_10K_<YEAR>.pdf` pattern, e.g.:

- `AAPL_10K_2024.pdf` — Apple Inc., fiscal year 2024
- `MSFT_10K_2024.pdf` — Microsoft Corporation, fiscal year 2024
- `GOOG_10K_2024.pdf` — Alphabet Inc., fiscal year 2024

PDFs are not committed to the repo (large, redistributable from EDGAR). To
populate locally:

```bash
mkdir -p samples
curl -L -o samples/AAPL_10K_2024.pdf https://www.sec.gov/Archives/edgar/data/320193/.../aapl-20240928.htm
# ...etc.
```

To upload to the workspace volume for an end-to-end test:

```bash
databricks fs cp samples/AAPL_10K_2024.pdf \
  dbfs:/Volumes/<catalog>/docintel_10k_dev/raw_filings/
```

The pipeline's file-arrival trigger picks up the upload and produces Gold rows
within ~10 minutes (SC-001).
