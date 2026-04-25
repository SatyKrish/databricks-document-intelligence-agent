-- Silver: parse each filing exactly once into a layout-aware VARIANT.
-- Constitution principle II (Parse Once, Extract Many) and FR-002.

CREATE OR REFRESH STREAMING TABLE silver_parsed_filings;

CREATE TEMPORARY VIEW silver_parsed_filings_changes AS
SELECT
  filename,
  ai_parse_document(content)                   AS parsed,
  current_timestamp()                          AS parsed_at,
  CASE
    WHEN ai_parse_document(content) IS NULL THEN 'error'
    WHEN try_variant_get(ai_parse_document(content), '$.metadata.partial', 'boolean') = TRUE THEN 'partial'
    ELSE 'ok'
  END                                          AS parse_status,
  try_variant_get(ai_parse_document(content), '$.metadata.error', 'string') AS parse_error
FROM STREAM(bronze_filings);

CREATE FLOW silver_parsed_filings_flow AS AUTO CDC INTO silver_parsed_filings
FROM STREAM(silver_parsed_filings_changes)
KEYS (filename)
SEQUENCE BY parsed_at
STORED AS SCD TYPE 1;
