-- Bronze: Auto Loader ingestion of raw 10-K PDFs from the UC volume.
-- FR-001 (event-triggered ingest), FR-013 (idempotent on filename),
-- spec edge case "PDFs > 50 MB" (rejected to bronze_filings_rejected).

CREATE OR REFRESH STREAMING TABLE bronze_filings (
  CONSTRAINT valid_size EXPECT (length <= ${max_pdf_bytes}) ON VIOLATION DROP ROW
)
COMMENT "Raw PDFs ingested from the raw_filings UC volume; > 50 MB filings drop to bronze_filings_rejected."
AS
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

CREATE OR REFRESH STREAMING TABLE bronze_filings_rejected
COMMENT "Filings rejected at Bronze for exceeding the size limit. Audit-only."
AS
SELECT
  reverse(split(path, '/'))[0]                AS filename,
  length,
  current_timestamp()                          AS rejected_at,
  'size_exceeds_limit'                         AS reason
FROM STREAM read_files(
  '/Volumes/${catalog}/${schema}/raw_filings',
  format => 'binaryFile',
  pathGlobFilter => '*.pdf'
)
WHERE length > ${max_pdf_bytes};
