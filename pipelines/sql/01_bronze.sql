-- Bronze: Auto Loader ingestion of raw 10-K PDFs from the UC volume.
-- FR-001 (event-triggered ingest), FR-013 (idempotent on filename),
-- spec edge case "PDFs > 50 MB" (rejected to bronze_filings_rejected).
--
-- Single Auto Loader source: skill databricks-pipelines/auto-loader-sql.md prescribes
-- one STREAM read_files() per source path. Both bronze_filings and
-- bronze_filings_rejected derive from the same checkpoint via a streaming view.

CREATE TEMPORARY STREAMING VIEW raw_pdf_arrivals AS
SELECT
  path,
  reverse(split(path, '/'))[0]                AS filename,
  content,
  length,
  modificationTime,
  current_timestamp()                          AS ingested_at
FROM STREAM read_files(
  '/Volumes/${catalog}/${schema}/raw_filings',
  format => 'binaryFile',
  pathGlobFilter => '*.pdf'
);

CREATE OR REFRESH STREAMING TABLE bronze_filings (
  CONSTRAINT valid_size EXPECT (length <= ${max_pdf_bytes}) ON VIOLATION DROP ROW
)
COMMENT "Raw PDFs ingested from the raw_filings UC volume; > 50 MB filings drop to bronze_filings_rejected."
AS SELECT
  path, filename, content, length, modificationTime, ingested_at
FROM STREAM(raw_pdf_arrivals);

CREATE OR REFRESH STREAMING TABLE bronze_filings_rejected
COMMENT "Filings rejected at Bronze for exceeding the size limit. Audit-only."
AS SELECT
  filename,
  length,
  ingested_at                                  AS rejected_at,
  'size_exceeds_limit'                         AS reason
FROM STREAM(raw_pdf_arrivals)
WHERE length > ${max_pdf_bytes};
