-- Gold: classify each parsed section, summarize, extract structured KPIs.
-- FR-003 (classify), FR-004 (extract), FR-013 (idempotent on filename).
-- Smoke-test variant: one row per filing (whole doc as one section).
-- A future iteration will explode parsed:sections[*] once the variant shape is verified.

CREATE OR REFRESH STREAMING TABLE gold_filing_sections_raw AS
SELECT
  s.filename,
  1                                             AS section_seq,
  'full_document'                               AS original_label,
  CAST(s.parsed AS STRING)                      AS section_text,
  s.parse_status,
  s.parsed_at
FROM STREAM(silver_parsed_filings) s
WHERE s.parse_status IN ('ok', 'partial');

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
    CONCAT('Summarize this 10-K in <=200 words preserving figures and named risks. SECTION:\n', section_text)
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
-- Materialized view (rebuild on source change) avoids the streaming-aggregation-watermark
-- requirement; idempotency is provided by GROUP BY filename.
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
  kpi.top_risks                                 AS top_risks_raw,
  TRY_CAST(kpi.extraction_confidence AS DOUBLE) AS extraction_confidence,
  sourced_at,
  current_timestamp()                          AS extracted_at
FROM extracted;
