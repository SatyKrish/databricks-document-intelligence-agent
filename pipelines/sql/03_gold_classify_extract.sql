-- Gold: classify each parsed section, summarize, extract structured KPIs.
-- FR-003 (classify), FR-004 (extract), FR-013 (idempotent on filename).

-- One row per parsed section. ai_parse_document returns sections in parsed:sections[*].
CREATE OR REFRESH STREAMING TABLE gold_filing_sections_raw AS
SELECT
  s.filename,
  CAST(section.section_seq AS INT)              AS section_seq,
  CAST(variant_get(section.value, '$.label', 'string')   AS STRING) AS original_label,
  CAST(variant_get(section.value, '$.text',  'string')   AS STRING) AS section_text,
  s.parse_status,
  s.parsed_at
FROM STREAM silver_parsed_filings s,
LATERAL variant_explode(s.parsed:sections) AS section(section_seq, value)
WHERE s.parse_status IN ('ok', 'partial');

CREATE OR REFRESH STREAMING TABLE gold_filing_sections;

APPLY CHANGES INTO gold_filing_sections
FROM STREAM (
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
  FROM gold_filing_sections_raw
)
KEYS (filename, section_seq)
SEQUENCE BY parsed_at
STORED AS SCD TYPE 1;

-- One row per filing: structured KPI extraction over MD&A + Financials concatenated.
CREATE OR REFRESH STREAMING TABLE gold_filing_kpis;

APPLY CHANGES INTO gold_filing_kpis
FROM STREAM (
  WITH joined AS (
    SELECT
      filename,
      ARRAY_JOIN(COLLECT_LIST(section_text), '\n\n') AS combined_text,
      MAX(parsed_at)                                  AS sourced_at
    FROM gold_filing_sections
    WHERE section_label IN ('MD&A', 'Financials')
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
    CAST(kpi:company_name AS STRING)             AS company_name,
    CAST(kpi:fiscal_year  AS INT)                AS fiscal_year,
    CAST(kpi:revenue       AS DECIMAL(20, 2))    AS revenue,
    CAST(kpi:ebitda        AS DECIMAL(20, 2))    AS ebitda,
    CAST(kpi:segment_revenue AS ARRAY<STRUCT<name STRING, revenue DECIMAL(20, 2)>>) AS segment_revenue,
    CAST(kpi:top_risks     AS ARRAY<STRING>)     AS top_risks,
    CAST(kpi:extraction_confidence AS DOUBLE)    AS extraction_confidence,
    sourced_at,
    current_timestamp()                          AS extracted_at
  FROM extracted
)
KEYS (filename)
SEQUENCE BY sourced_at
STORED AS SCD TYPE 1;
