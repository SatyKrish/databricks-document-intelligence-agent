"""Generate the GitHub social preview image at docs/social-preview.png.

Run:
    .venv/bin/python docs/_social_preview.py

Output: 1280x640 PNG (GitHub's recommended dimensions). The image is
committed alongside this script so the social preview is reproducible.

Upload to GitHub via:
    Repo Settings → General → Social preview → Upload an image.
(GitHub's API doesn't expose a clean upload endpoint, so this is a one-time
manual step per fork.)
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


W, H = 1280, 640
BG = "#0E1525"           # deep navy
FG = "#F5F7FA"           # near-white
DIM = "#9AA5B8"          # medium gray-blue
ACCENT = "#FF3621"       # Databricks orange
LINE = "#252D3F"         # subtle separator

# Arial bundles ship on macOS, support a wide glyph set including arrows,
# and have explicit Regular/Bold/Black files (no .ttc index guessing).
FONT_REG = "/System/Library/Fonts/Supplemental/Arial.ttf"
FONT_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
FONT_BLACK = "/System/Library/Fonts/Supplemental/Arial Black.ttf"

OUT = Path(__file__).parent / "social-preview.png"


def font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont:
    path = {"regular": FONT_REG, "bold": FONT_BOLD, "black": FONT_BLACK}[weight]
    return ImageFont.truetype(path, size)


def text_w(draw: ImageDraw.ImageDraw, s: str, f: ImageFont.FreeTypeFont) -> int:
    bbox = draw.textbbox((0, 0), s, font=f)
    return bbox[2] - bbox[0]


def main() -> None:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    margin = 80

    # Accent bar — small horizontal pill in the top-left, evokes Databricks orange.
    d.rectangle([(margin, margin), (margin + 64, margin + 6)], fill=ACCENT)

    # Eyebrow — small, dim, all-caps.
    eyebrow = font(20, "bold")
    d.text((margin, margin + 22), "DATABRICKS", font=eyebrow, fill=DIM)

    # Title — two-line, big & black.
    title_f = font(80, "black")
    d.text((margin, margin + 60), "Document Intelligence", font=title_f, fill=FG)
    d.text((margin, margin + 60 + 92), "Agent", font=title_f, fill=FG)

    # Subtitle — medium, dim.
    subtitle_f = font(32, "regular")
    d.text((margin, margin + 60 + 92 + 110), "Reference Implementation", font=subtitle_f, fill=DIM)

    # One-line architecture summary, near bottom. ASCII arrows guarantee
    # glyph coverage across any future font swap.
    arch_f = font(22, "bold")
    arch_text = "ai_parse_document  ->  typed KPIs  ->  Vector Search  ->  cited agent on Mosaic AI"
    d.text((margin, H - margin - 80), arch_text, font=arch_f, fill=FG)

    # Separator + footer.
    d.line([(margin, H - margin - 40), (W - margin, H - margin - 40)], fill=LINE, width=1)

    footer_f = font(18, "regular")
    footer_text = "MIT  ·  Spec-Kit  +  Claude Code  ·  github.com/SatyKrish/databricks-document-intelligence-agent"
    d.text((margin, H - margin - 22), footer_text, font=footer_f, fill=DIM)

    img.save(OUT, "PNG", optimize=True)
    print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes, {W}x{H})")


if __name__ == "__main__":
    main()
