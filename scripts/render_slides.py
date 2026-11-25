# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright>=1.50"]
# ///
# ruff: noqa: E501  (slide and caption copy is prose)
"""Render the deck to one PNG per slide and a PDF, for checking by eye (brief VTF-09, section 2).

    uv run scripts/render_slides.py [docs/slides/vtf_slides.pptx]

macOS only. Keynote's scripting needs an automation permission an unattended run cannot grant,
and LibreOffice is not installed, so this uses two pieces already on the Mac:

1. Quick Look (`qlmanage -p -o`) converts the .pptx to HTML with Apple's Office importer: every
   slide as absolutely placed text boxes, shapes and images, the way PowerPoint lays them out.
2. Headless Chrome (Playwright) renders that HTML with the template's real font, League Spartan,
   loaded from Google Fonts (the font Google Slides uses for this template; it is not installed
   on this Mac). If the font cannot load, Avenir Next stands in and the script says so.

Writes out/slides/png/slide_NN.png (1920 x 1080) and docs/slides/vtf_slides.pdf (vector text,
one 10 x 5.625 in page per slide). Google Slides and PowerPoint may wrap a line differently;
the boxes leave slack for that.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PPTX = ROOT / "docs/slides/vtf_slides.pptx"
PDF = ROOT / "docs/slides/vtf_slides.pdf"
PNG_DIR = ROOT / "out/slides/png"
GOOGLE_CSS = (
    "https://fonts.googleapis.com/css2?family=League+Spartan:wght@400;500;600;700&display=block"
)
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140 Safari/537.36"
SLIDE_W, SLIDE_H = 720, 405  # Quick Look lays slides out in points


def quicklook_html(pptx: Path, work: Path) -> Path:
    subprocess.run(["qlmanage", "-p", "-o", str(work), str(pptx)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)  # fmt: skip
    html = work / f"{pptx.name}.qlpreview" / "Preview.html"
    if not html.is_file():
        sys.exit(f"render_slides: Quick Look wrote no preview for {pptx}")
    # Quick Look draws shapes it cannot express in HTML (rounded boxes, arrows) as small PDFs,
    # which an <img> cannot show: convert each to SVG (vector, so the PDF stays sharp)
    text = html.read_text(encoding="utf-8")
    for pdf in sorted(html.parent.glob("Attachment*.pdf")):
        svg = pdf.with_suffix(".svg")
        if shutil.which("pdftocairo"):
            subprocess.run(["pdftocairo", "-svg", str(pdf), str(svg)], check=True)
        else:  # Poppler missing: a bitmap is still fine for checking
            subprocess.run(["sips", "-s", "format", "png", str(pdf), "--out", str(svg.with_suffix(".png"))],
                           check=True, stdout=subprocess.DEVNULL)  # fmt: skip
            svg = svg.with_suffix(".png")
        text = text.replace(f'"{pdf.name}"', f'"{svg.name}"')
    html.write_text(text, encoding="utf-8")
    return html


def font_css() -> tuple[str, str]:
    """@font-face rules mapping the template's font names onto League Spartan from Google Fonts."""
    try:
        req = urllib.request.Request(GOOGLE_CSS, headers={"User-Agent": UA})
        css = urllib.request.urlopen(req, timeout=20).read().decode()
    except OSError as e:
        fallback = "\n".join(
            f'@font-face {{ font-family: "{fam}"; src: local("Avenir Next {face}"); font-weight: {w}; }}'
            for fam, faces in (("League Spartan", [("Regular", 400), ("Bold", 700)]),
                               ("League Spartan Medium", [("Medium", 400), ("Bold", 700)]))
            for face, w in faces)  # fmt: skip
        return fallback, f"Avenir Next (League Spartan did not load: {e})"
    blocks = re.findall(r"@font-face\s*{[^}]*}", css)
    out = []
    for b in blocks:
        src = re.search(r"src:\s*([^;]+);", b).group(1)
        rng = re.search(r"unicode-range:\s*([^;]+);", b)
        rng = f" unicode-range: {rng.group(1)};" if rng else ""
        # a variable font: a single weight in the descriptor pins the wght axis to it
        for fam, normal in (("League Spartan", 400), ("League Spartan Medium", 500)):
            for w in (normal, 700):
                out.append(f'@font-face {{ font-family: "{fam}"; font-style: normal; font-weight: {w}; '
                           f"src: {src};{rng} }}")  # fmt: skip
    return "\n".join(out), "League Spartan (Google Fonts)"


PRINT_CSS = f"""
@page {{ size: {SLIDE_W}px {SLIDE_H}px; margin: 0; }}
@media print {{
  body {{ margin: 0 !important; background: white !important; }}
  div.slide {{ margin: 0 !important; -webkit-box-shadow: none !important; break-after: page; }}
}}
"""


def render(pptx: Path = PPTX) -> None:
    PNG_DIR.mkdir(parents=True, exist_ok=True)
    for old in PNG_DIR.glob("slide_*.png"):
        old.unlink()
    with tempfile.TemporaryDirectory() as tmp:
        html = quicklook_html(pptx, Path(tmp))
        fonts, which = font_css()
        page_html = html.read_text(encoding="utf-8")
        page_html = page_html.replace("<head>", f"<head><style>{fonts}\n{PRINT_CSS}</style>", 1)
        html.write_text(page_html, encoding="utf-8")
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception:  # no Playwright Chromium for this version: use the installed Chrome
                browser = p.chromium.launch(channel="chrome")
            page = browser.new_page(viewport={"width": SLIDE_W + 40, "height": SLIDE_H + 40},
                                    device_scale_factor=1920 / SLIDE_W)  # fmt: skip
            page.goto(html.as_uri())
            page.wait_for_load_state("networkidle")
            page.evaluate("document.fonts.ready.then(() => true)")
            loaded = page.evaluate("[...document.fonts].filter(f => f.status === 'loaded').length")
            slides = page.query_selector_all("div.slide")
            for i, el in enumerate(slides, 1):
                el.screenshot(path=str(PNG_DIR / f"slide_{i:02d}.png"))
            page.emulate_media(media="print")
            page.pdf(path=str(PDF), width=f"{SLIDE_W}px", height=f"{SLIDE_H}px", print_background=True,
                     margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})  # fmt: skip
            browser.close()
    print(f"{len(slides)} slides -> {PNG_DIR}/slide_NN.png and {PDF} ({PDF.stat().st_size // 1024} KB); "
          f"font: {which}, {loaded} font faces loaded")  # fmt: skip


if __name__ == "__main__":
    if not shutil.which("qlmanage"):
        sys.exit("render_slides: needs macOS Quick Look (qlmanage)")
    render(Path(sys.argv[1]) if len(sys.argv) > 1 else PPTX)
