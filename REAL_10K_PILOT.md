# Real 10-K Pilot

The default corpus is synthetic so the reference is reproducible. Before production use, run a small pilot with public SEC EDGAR filings.

## Recommended Pilot Corpus

Choose 5-10 filings that cover:

- Large technology issuer with many segments.
- Financial issuer with dense notes.
- Manufacturer or retailer with supply-chain risk.
- Filing with tables and footnotes.
- Filing with imperfect OCR or unusual section layout.

## Upload

```bash
databricks fs cp ./samples/AAPL_10K_2024.pdf \
  dbfs:/Volumes/<catalog>/<schema>/raw_filings/
```

Use filenames like `<TICKER>_10K_<YEAR>.pdf`.

## Validate Pipeline Output

```sql
SELECT filename, company_name, fiscal_year, revenue, ebitda,
       segment_revenue, top_risks, extraction_confidence
FROM <catalog>.<schema>.gold_filing_kpis
ORDER BY filename;

SELECT filename, section_label, count(*) AS n, avg(quality_score) AS avg_quality
FROM <catalog>.<schema>.gold_filing_sections_indexable
GROUP BY filename, section_label
ORDER BY filename, section_label;
```

Manually review at least:

- Revenue and EBITDA.
- Segment revenue array.
- Top risk themes.
- Section labels for Risk, MD&A, Financials, and Notes.
- Whether any useful filing sections were excluded by the quality threshold.

## Validate Agent Behavior

Ask at least:

- One factual numeric question per filing.
- One top-risk question per filing.
- One segment-revenue question per filing.
- Two cross-company comparison questions.
- One question about a company not in the corpus.

Expected:

- Grounded answers cite relevant filings.
- Unknown-company questions return the canonical no-source response.
- Cross-company tables only include companies with grounded data.

## Record Pilot Evidence

Capture:

- Date and workspace.
- Filing list and file sizes.
- Pipeline duration.
- CLEARS run ID.
- Latency p95.
- Notable extraction failures.
- Cost estimate for parsing, scoring, embedding, serving, and app usage.
