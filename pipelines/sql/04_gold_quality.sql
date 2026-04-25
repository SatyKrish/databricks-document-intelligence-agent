-- Quality rubric (Constitution principle IV). Five 0-6 dimensions, threshold ≥ 22/30.
-- FR-005, edge case "files failing the quality rubric".

CREATE OR REFRESH MATERIALIZED VIEW gold_filing_quality AS
WITH scored AS (
  SELECT
    g.filename,
    g.section_seq,
    g.section_text,
    g.original_label,
    g.section_label,
    -- Each ai_query returns a stringified integer 0..6
    CAST(ai_query(
      'databricks-meta-llama-3-3-70b-instruct',
      CONCAT('Score parse_completeness 0-6 for this 10-K section text (6=fully readable, 0=garbled). Reply with the integer only.\n', g.section_text)
    ) AS INT) AS parse_completeness,
    CAST(ai_query(
      'databricks-meta-llama-3-3-70b-instruct',
      CONCAT('Score layout_fidelity 0-6 for this section (6=tables/lists preserved, 0=lost structure). Reply with the integer only.\n', g.section_text)
    ) AS INT) AS layout_fidelity,
    CAST(ai_query(
      'databricks-meta-llama-3-3-70b-instruct',
      CONCAT('Score ocr_confidence 0-6 (6=clean text, 0=heavy OCR artefacts). Reply with the integer only.\n', g.section_text)
    ) AS INT) AS ocr_confidence,
    CAST(ai_query(
      'databricks-meta-llama-3-3-70b-instruct',
      CONCAT('Score section_recognizability 0-6 (6=clearly canonical 10-K section, 0=ambiguous). Section label: ',
             g.section_label, '. Reply with the integer only.\n', g.section_text)
    ) AS INT) AS section_recognizability,
    CAST(ai_query(
      'databricks-meta-llama-3-3-70b-instruct',
      CONCAT('Score kpi_extractability 0-6 (6=numeric KPIs explicit, 0=none). Reply with the integer only.\n', g.section_text)
    ) AS INT) AS kpi_extractability
  FROM gold_filing_sections g
)
SELECT
  filename,
  section_seq,
  parse_completeness,
  layout_fidelity,
  ocr_confidence,
  section_recognizability,
  kpi_extractability,
  COALESCE(parse_completeness, 0)
    + COALESCE(layout_fidelity, 0)
    + COALESCE(ocr_confidence, 0)
    + COALESCE(section_recognizability, 0)
    + COALESCE(kpi_extractability, 0)            AS quality_score,
  named_struct(
    'parse_completeness', parse_completeness,
    'layout_fidelity', layout_fidelity,
    'ocr_confidence', ocr_confidence,
    'section_recognizability', section_recognizability,
    'kpi_extractability', kpi_extractability
  )                                              AS quality_breakdown,
  current_timestamp()                            AS scored_at
FROM scored;

-- Index source: gold sections joined with quality, filtered to embed_eligible only.
-- Streaming table (not MV) because Vector Search Delta-Sync needs a source it
-- can track via Change Data Feed; CDF is unsupported on materialized views.
-- Stream-static join: gold_filing_sections is the streaming side, gold_filing_quality
-- (MV) is the static lookup. The WHERE clause enforces FR-005 / SC-006: only sections
-- at or above the quality threshold reach the index.
CREATE OR REFRESH STREAMING TABLE gold_filing_sections_indexable
TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true')
AS
SELECT
  CONCAT(s.filename, '#', CAST(s.section_seq AS STRING)) AS section_uid,
  s.filename,
  s.section_seq,
  s.original_label,
  s.section_label,
  s.summary,
  q.quality_score
FROM STREAM(gold_filing_sections) s
LEFT JOIN gold_filing_quality q
  ON s.filename = q.filename AND s.section_seq = q.section_seq
WHERE q.quality_score >= ${quality_threshold} AND s.parse_status = 'ok';
