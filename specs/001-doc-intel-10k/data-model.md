# Phase 1 Data Model

All Delta tables live under the bundle-parameterized `${var.catalog}.${var.schema}`. Lakebase tables live in the bundle-managed Lakebase database `${var.catalog}_state`.

## Bronze

### `bronze_filings` (streaming table)

Source: Auto Loader `cloudFiles` over `volume:/Volumes/${var.catalog}/${var.schema}/raw_filings/*.pdf`, `format=BINARYFILE`.

| Column | Type | Notes |
|---|---|---|
| `path` | STRING | Full volume path |
| `filename` | STRING | Basename, used as logical PK throughout pipeline |
| `content` | BINARY | Raw PDF bytes |
| `length` | BIGINT | File size for SC-006 logging and 50 MB ceiling |
| `modificationTime` | TIMESTAMP | From Auto Loader |
| `ingested_at` | TIMESTAMP | Default `current_timestamp()` |

## Silver

### `silver_parsed_filings` (streaming table, CDC keyed on `filename`)

Built by `02_silver_parse.sql`: `SELECT filename, ai_parse_document(content) AS parsed, current_timestamp() AS parsed_at FROM bronze_filings`.

| Column | Type | Notes |
|---|---|---|
| `filename` | STRING (PK) | CDC apply-changes key |
| `parsed` | VARIANT | Layout-aware structured output from `ai_parse_document` |
| `parsed_at` | TIMESTAMP | |
| `parse_status` | STRING | `ok` / `partial` / `error`; partial means OCR salvage |
| `parse_error` | STRING | NULL on success |

## Gold

### `gold_filing_sections` (streaming table, CDC keyed on `(filename, section_seq)`)

One row per parsed section. Built by `03_gold_classify_extract.sql` from `silver_parsed_filings.parsed:sections[*]`.

| Column | Type | Notes |
|---|---|---|
| `filename` | STRING | FK to silver |
| `section_seq` | INT | Original ordering within filing |
| `original_label` | STRING | As parsed |
| `section_label` | STRING | One of `MD&A`, `Risk`, `Financials`, `Notes`, `Other`; from `ai_classify` |
| `section_text` | STRING | Plain text of the section |
| `summary` | STRING | LLM-generated 200-word summary; what gets embedded |
| `embed_eligible` | BOOLEAN | TRUE iff `quality_score >= 22` AND `parse_status = 'ok'` |
| `created_at` | TIMESTAMP | |

### `gold_filing_kpis` (streaming table, CDC keyed on `filename`)

One row per filing. Built by `03_gold_classify_extract.sql` calling `ai_extract` against the concatenated MD&A + Financials sections with `kpi-schema.json`.

| Column | Type | Notes |
|---|---|---|
| `filename` | STRING (PK) | |
| `company_name` | STRING | From cover page |
| `fiscal_year` | INT | |
| `revenue` | DECIMAL(20, 2) | USD |
| `ebitda` | DECIMAL(20, 2) | USD; nullable if not disclosed |
| `segment_revenue` | ARRAY<STRUCT<name STRING, revenue DECIMAL(20, 2)>> | |
| `top_risks` | ARRAY<STRING> | Up to 10 |
| `extraction_confidence` | DOUBLE | 0–1 from `ai_extract` |
| `extracted_at` | TIMESTAMP | |

### `gold_filing_quality` (materialized view)

Built by `04_gold_quality.sql`. One row per `(filename, section_seq)` from `gold_filing_sections`.

| Column | Type | Notes |
|---|---|---|
| `filename` | STRING | |
| `section_seq` | INT | |
| `parse_completeness` | INT | 0–6, from `ai_query` against parsed VARIANT |
| `layout_fidelity` | INT | 0–6 |
| `ocr_confidence` | INT | 0–6 |
| `section_recognizability` | INT | 0–6 |
| `kpi_extractability` | INT | 0–6 |
| `quality_score` | INT | Sum of above; 0–30 |
| `quality_breakdown` | STRUCT | All 5 dims as struct for audit |
| `scored_at` | TIMESTAMP | |

A view `gold_filing_sections_with_quality` joins sections + quality and is the source for the Vector Search index.

## Vector Search

### Index `${var.catalog}.${var.schema}.filings_summary_idx` (Delta-Sync)

- Source: `gold_filing_sections_with_quality` filtered `WHERE embed_eligible = true`
- Embedding column: `summary`
- Embedding model: workspace default (`databricks-bge-large-en` or workspace-configured Mosaic embedding endpoint)
- Returned columns at retrieval: `filename`, `section_label`, `original_label`, `summary`, `quality_score`

## Lakebase (transactional state)

### `conversation_history`

| Column | Type | Notes |
|---|---|---|
| `conversation_id` | UUID PK | One row per session |
| `user_email` | STRING | From identity passthrough |
| `started_at` | TIMESTAMPTZ | |
| `last_turn_at` | TIMESTAMPTZ | |

### `query_logs`

| Column | Type | Notes |
|---|---|---|
| `turn_id` | UUID PK | |
| `conversation_id` | UUID FK | |
| `question` | TEXT | |
| `answer` | TEXT | |
| `citations` | JSONB | Array of `{filename, section_label, score}` |
| `latency_ms` | INT | |
| `agent_path` | STRING | `knowledge_assistant` / `analyst` / `supervisor` |
| `created_at` | TIMESTAMPTZ | |

### `feedback`

| Column | Type | Notes |
|---|---|---|
| `feedback_id` | UUID PK | |
| `turn_id` | UUID FK | |
| `user_email` | STRING | |
| `rating` | STRING | `up` / `down` |
| `comment` | TEXT | Optional |
| `created_at` | TIMESTAMPTZ | |

## Eval

### `evals/dataset.jsonl`

One JSON object per line:

```json
{"id": "p2-001", "category": "P2", "question": "...", "expected_filename": "AAPL_10K_2024.pdf", "expected_section": "Risk", "expected_answer_keywords": ["..."], "min_citations": 1}
```

10 rows for `P3` carry `expected_companies: [...]` and `expected_table_columns: [...]` instead of section/keyword fields.

## State transitions

```
PDF in volume
  └─ Auto Loader → bronze_filings (status: ingested)
       └─ ai_parse_document → silver_parsed_filings (parse_status)
            └─ ai_classify + ai_extract → gold_filing_{sections,kpis}
                 └─ ai_query rubric → gold_filing_quality (quality_score)
                      └─ quality_score threshold → Vector Search index sync
                           └─ Agent Bricks Knowledge Assistant + Supervisor Agent
                                └─ AI Gateway + OBO
                                     └─ Streamlit App turn
                                          └─ Lakebase query_logs + feedback
```
