-- Gold: classify each parsed section, summarize, extract structured KPIs.
-- FR-003 (classify), FR-004 (extract), FR-013 (idempotent on filename).
--
-- Section explosion: the silver VARIANT carries a `$.sections[*]` array with
-- {label, text} per parsed section. We POSEXPLODE that into per-section rows.
-- Filings whose VARIANT does not produce a usable sections array are represented
-- as a single full_document row so we never silently drop a parsed filing.

CREATE OR REFRESH STREAMING TABLE gold_filing_sections_raw AS
WITH sectioned AS (
  SELECT
    s.filename,
    pos + 1                                                       AS section_seq,
    CAST(variant_get(section, '$.label', 'string') AS STRING)     AS original_label,
    CAST(variant_get(section, '$.text',  'string') AS STRING)     AS section_text,
    s.parse_status,
    s.parsed_at
  FROM STREAM(silver_parsed_filings) s
  LATERAL VIEW POSEXPLODE(
    CAST(variant_get(s.parsed, '$.sections') AS ARRAY<VARIANT>)
  ) t AS pos, section
  WHERE s.parse_status IN ('ok', 'partial')
    AND variant_get(s.parsed, '$.sections') IS NOT NULL
),
whole_document AS (
  -- Filings whose parsed VARIANT lacks $.sections still get one row so
  -- downstream classification/extraction can run.
  SELECT
    s.filename,
    1                                                             AS section_seq,
    'full_document'                                               AS original_label,
    CAST(s.parsed AS STRING)                                      AS section_text,
    s.parse_status,
    s.parsed_at
  FROM STREAM(silver_parsed_filings) s
  WHERE s.parse_status IN ('ok', 'partial')
    AND variant_get(s.parsed, '$.sections') IS NULL
)
SELECT * FROM sectioned
UNION ALL
SELECT * FROM whole_document;

CREATE OR REFRESH STREAMING TABLE gold_filing_sections
TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true');

CREATE TEMPORARY VIEW gold_filing_sections_changes AS
SELECT
  filename,
  section_seq,
  original_label,
  section_text,
  ai_classify(
    section_text,
    ARRAY('MD&A', 'Risk', 'Financials', 'Notes', 'Other')
  )                                            AS section_label,
  ai_query(
    'databricks-meta-llama-3-3-70b-instruct',
    CONCAT('Summarize this 10-K section in <=200 words preserving figures and named risks. SECTION:\n', section_text)
  )                                            AS summary,
  parse_status,
  parsed_at,
  current_timestamp()                          AS created_at
FROM STREAM(gold_filing_sections_raw);

CREATE FLOW gold_filing_sections_flow AS AUTO CDC INTO gold_filing_sections
FROM STREAM(gold_filing_sections_changes)
KEYS (filename, section_seq)
SEQUENCE BY parsed_at
STORED AS SCD TYPE 1;

-- One row per filing: structured KPI extraction.
-- segment_revenue and top_risks are stored as both _raw VARIANT (for debugging)
-- and typed columns (the contract per kpi-schema.json + data-model.md).
-- ai_extract returns string-encoded JSON; from_json with explicit schema
-- materializes the typed array.
CREATE OR REFRESH MATERIALIZED VIEW gold_filing_kpis AS
WITH joined AS (
  SELECT
    filename,
    ARRAY_JOIN(COLLECT_LIST(section_text), '\n\n') AS combined_text,
    MAX(parsed_at)                                  AS sourced_at
  FROM gold_filing_sections
  GROUP BY filename
),
extracted AS (
  SELECT
    filename,
    ai_extract(
      combined_text,
      ARRAY(
        'company_name', 'fiscal_year',
        'revenue', 'ebitda',
        'segment_revenue', 'top_risks',
        'extraction_confidence'
      )
    )                                          AS kpi,
    sourced_at
  FROM joined
)
SELECT
  filename,
  kpi.company_name                              AS company_name,
  TRY_CAST(kpi.fiscal_year AS INT)              AS fiscal_year,
  TRY_CAST(kpi.revenue AS DECIMAL(20, 2))       AS revenue,
  TRY_CAST(kpi.ebitda  AS DECIMAL(20, 2))       AS ebitda,
  kpi.segment_revenue                           AS segment_revenue_raw,
  from_json(
    CAST(kpi.segment_revenue AS STRING),
    'ARRAY<STRUCT<name: STRING, revenue: DECIMAL(20, 2)>>'
  )                                             AS segment_revenue,
  kpi.top_risks                                 AS top_risks_raw,
  from_json(
    CAST(kpi.top_risks AS STRING),
    'ARRAY<STRING>'
  )                                             AS top_risks,
  TRY_CAST(kpi.extraction_confidence AS DOUBLE) AS extraction_confidence,
  sourced_at,
  current_timestamp()                          AS extracted_at
FROM extracted;
