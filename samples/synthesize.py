"""Generate synthetic 10-K PDFs for smoke tests and CI eval.

Produces three synthetic full-content filings (ACME, BETA, GAMMA) plus one
deliberately low-quality filing (garbage). The synthetic filings keep the
canonical 10-K section structure (MD&A, Risk Factors, Financial Statements,
Notes) so the SDP pipeline's section explosion + KPI extraction has real
material to work with. The garbage filing has no financial content so the
quality rubric must score it below threshold (SC-006).

Run:
    .venv/bin/python samples/synthesize.py

Output: samples/{ACME,BETA,GAMMA}_10K_2024.pdf and samples/garbage_10K_2024.pdf
"""

from __future__ import annotations

from pathlib import Path

from fpdf import FPDF


_OUT_DIR = Path(__file__).parent


def _filing(pdf: FPDF, *, company: str, fiscal_year: int, revenue_b: float,
            ebitda_b: float, gross_margin_pct: float, segments: list[tuple[str, float, float]],
            top_risks: list[str]) -> None:
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, f"{company.upper()} CORPORATION - FORM 10-K - FISCAL YEAR {fiscal_year}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5, f"Company: {company} Corporation. Fiscal year ended: September 30, {fiscal_year}.")
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Item 7. Management's Discussion and Analysis (MD&A)", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5,
        f"Total net sales for fiscal {fiscal_year} were ${revenue_b:.1f} billion, "
        f"reflecting growth in our core operating segments. Operating income was "
        f"${ebitda_b * 0.85:.1f} billion and EBITDA was ${ebitda_b:.1f} billion. "
        f"Gross margin was {gross_margin_pct:.1f}%. The Company continues to invest "
        f"in research and development, with R&D spend at ${revenue_b * 0.13:.1f} billion. "
        f"Cash from operations was ${revenue_b * 0.28:.1f} billion."
    )
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Item 1A. Risk Factors", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5,
        f"The Company is subject to a wide range of risks. Among the most significant "
        f"risks management has identified for fiscal {fiscal_year} are: "
        + "; ".join(f"({i+1}) {risk}" for i, risk in enumerate(top_risks))
        + ". Failure to manage any of these could have a material adverse effect on "
        f"financial condition, results of operations, and cash flows."
    )
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Item 8. Financial Statements and Supplementary Data", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(80, 6, "Segment", border=1)
    pdf.cell(40, 6, "Revenue ($B)", border=1)
    pdf.cell(40, 6, "Operating Income ($B)", border=1, new_x="LMARGIN", new_y="NEXT")
    for name, rev, op in segments:
        pdf.cell(80, 6, name, border=1)
        pdf.cell(40, 6, f"${rev:.1f}", border=1)
        pdf.cell(40, 6, f"${op:.1f}", border=1, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.multi_cell(0, 5,
        f"Total assets at fiscal year end were ${revenue_b * 4:.1f} billion. "
        f"Total liabilities were ${revenue_b * 2.9:.1f} billion. "
        f"Stockholders' equity was ${revenue_b * 1.1:.1f} billion."
    )
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Notes to Consolidated Financial Statements", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5,
        f"Note 1 - Summary of Significant Accounting Policies. The consolidated "
        f"financial statements include the accounts of {company} Corporation and its "
        f"wholly owned subsidiaries.\n"
        f"Note 2 - Stock-Based Compensation. The Company recognized "
        f"${revenue_b * 0.04:.1f} billion of stock-based compensation expense in fiscal "
        f"{fiscal_year}.\n"
        f"Note 3 - Income Taxes. The provision for income taxes was "
        f"${revenue_b * 0.06:.1f} billion, an effective rate of approximately 19.7%.\n"
        f"Note 4 - Stock Repurchases. The Company repurchased "
        f"${revenue_b * 0.10:.1f} billion of its common stock during fiscal {fiscal_year}."
    )


def _write_filing(filename: str, **kwargs) -> None:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    _filing(pdf, **kwargs)
    out = _OUT_DIR / filename
    pdf.output(str(out))
    print(f"wrote {out}")


def _write_garbage() -> None:
    """Low-quality filing that the 5-dim rubric must score below threshold (SC-006).

    No section headers, no financial figures, mostly noise text. The pipeline's
    quality_score should land well below 22/30 so this filing is excluded from
    the Vector Search index even though it lands in Gold.
    """
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5,
        "asdfg qwerty zxcvb mnbvc poiuy lkjhg zzzz xxxx yyyy aaaa bbbb cccc "
        "this document does not contain any financial reporting content and "
        "should be rejected by the quality rubric. lorem ipsum dolor sit amet "
        "consectetur adipiscing elit sed do eiusmod tempor incididunt ut labore "
        "et dolore magna aliqua. random words: blue green orange purple banana "
        "lamp coffee mountain river. there are no section headers no kpis no "
        "tables no risk factors no notes no md&a. this is intentionally noise."
    )
    out = _OUT_DIR / "garbage_10K_2024.pdf"
    pdf.output(str(out))
    print(f"wrote {out}")


if __name__ == "__main__":
    _write_filing(
        "ACME_10K_2024.pdf",
        company="ACME", fiscal_year=2024,
        revenue_b=94.2, ebitda_b=32.1, gross_margin_pct=44.2,
        segments=[("Products", 66.4, 18.2), ("Services", 22.1, 15.5), ("Cloud Platform", 5.7, 0.4)],
        top_risks=[
            "macroeconomic conditions including inflation and recessionary pressure",
            "competitive pressure from new entrants in artificial intelligence and cloud",
            "supply chain concentration in a small number of geographies",
            "cybersecurity threats including ransomware",
            "regulation of digital marketplaces and antitrust enforcement",
            "climate change exposure to physical assets",
        ],
    )
    _write_filing(
        "BETA_10K_2024.pdf",
        company="BETA", fiscal_year=2024,
        revenue_b=212.0, ebitda_b=88.5, gross_margin_pct=69.8,
        segments=[("Productivity & Cloud", 109.0, 47.0), ("Personal Computing", 60.0, 18.0), ("Intelligent Cloud", 43.0, 23.5)],
        top_risks=[
            "competition in cloud services and platform ecosystems",
            "AI model reliability and customer adoption uncertainty",
            "regulatory scrutiny of platform dominance in the EU and US",
            "cybersecurity and supply chain attacks against enterprise customers",
            "dependence on third-party hardware partners",
        ],
    )
    _write_filing(
        "GAMMA_10K_2024.pdf",
        company="GAMMA", fiscal_year=2024,
        revenue_b=305.0, ebitda_b=110.0, gross_margin_pct=58.0,
        segments=[("Search & Other", 200.0, 88.0), ("YouTube Ads", 32.0, 9.0), ("Cloud", 36.0, 1.5), ("Other Bets", 1.5, -3.0)],
        top_risks=[
            "regulatory and antitrust enforcement actions globally",
            "shifts in advertising market and competition from generative AI",
            "data privacy regulation in the EU, US, and Asia",
            "platform abuse, content moderation challenges, and reputational risk",
            "investment risk in Other Bets and long-horizon research",
        ],
    )
    _write_garbage()
