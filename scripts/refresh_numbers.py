# /// script
# requires-python = ">=3.12"
# dependencies = ["python-pptx>=1.0.2", "pillow>=10", "pyyaml>=6", "playwright>=1.50"]
# ///
# ruff: noqa: E501
"""Fill every demo-pack placeholder from findings.json and Phase 2's outputs (brief VTF-09, section 7).

    uv run scripts/refresh_numbers.py [--twins out/twins_real] [--no-render]
    uv run scripts/refresh_numbers.py --check      # list what is still a placeholder; exit 1 if any

Reads, through scripts/demo_numbers.py:
  docs/reports/v1/findings.json            frozen v1 numbers
  docs/reports/v1/human_vs_models.json     {{AUDIT}}      (scripts/audit_vs_models.py)
  <twins>/sim_to_real/summary.json         {{SIM2REAL}}   (vtf twin sim-to-real)
  <twins>/pairs.jsonl and eval results     {{REAL_TRAP}}  (the H1 hero photo)
  docs/submission_inputs.json              team name, team leader, video and slides links

and rewrites, idempotently (run it again whenever a number changes):
  docs/slides/vtf_slides.pptx and .pdf     rebuilt (scripts/build_slides.py, render_slides.py)
  docs/video/script.md, shot_list.md/.pdf, captions.srt   regenerated (scripts/video_kit.py docs)
  docs/submission.md, docs/QA.md           the <!--ph:KEY-->...<!--/ph--> spans
  README.md                                the headline's human-baseline and real-photo lines

A value that is not available yet stays {{KEY}} (invisible in the README). Nothing calls a model.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_slides  # noqa: E402
import video_kit  # noqa: E402
from demo_numbers import ROOT, numbers  # noqa: E402

MARKED = [ROOT / "docs/submission.md", ROOT / "docs/QA.md", ROOT / "README.md"]
SPAN = re.compile(r"<!--ph:([A-Z0-9_]+)-->(.*?)<!--/ph-->", re.S)
TOKEN = re.compile(r"\{\{[A-Z0-9_]+\}\}")


def readme_values(n: dict) -> dict[str, str]:
    """README lines appear only once their numbers exist (no visible placeholder for judges)."""
    out = {"README_HUMAN": "", "README_REAL": ""}
    if n.get("AUDIT"):
        out["README_HUMAN"] = f"\n- **Human baseline:** {n['AUDIT']} ([human_vs_models.md](docs/reports/v1/human_vs_models.md))"
    if n.get("SIM2REAL"):
        trap = f" {n['REAL_TRAP']}" if n.get("REAL_TRAP") else ""
        out["README_REAL"] = f"\n- **Real photos (physical twins):** {n['SIM2REAL']}{trap} {n.get('SIM2REAL_ENDING') or ''}".rstrip()
    return out


def fill_marked(path: Path, n: dict) -> tuple[int, list[str]]:
    text = path.read_text(encoding="utf-8")
    values = {**n, **readme_values(n)}

    def sub(m: re.Match) -> str:
        key = m.group(1)
        v = values.get(key)
        if key.startswith("README_"):
            v = v or ""
        return f"<!--ph:{key}-->{v if v is not None else '{{' + key + '}}'}<!--/ph-->"

    new, count = SPAN.subn(sub, text)
    if new != text:
        path.write_text(new, encoding="utf-8")
    left = sorted({m.group(1) for m in SPAN.finditer(new) if TOKEN.search(m.group(2))})
    return count, left


def placeholders_left() -> dict[str, list[str]]:
    from pptx import Presentation

    found: dict[str, list[str]] = {}
    prs = Presentation(str(build_slides.OUT))
    toks = {t for s in prs.slides for sh in s.shapes if sh.has_text_frame for t in TOKEN.findall(sh.text_frame.text)}
    if any(sh.name == "PHOTO_H1" for s in prs.slides for sh in s.shapes):
        toks.add("(H1 photo)")
    found["slides"] = sorted(toks)
    for p in [ROOT / "docs/video/captions.srt", ROOT / "docs/video/script.md", *MARKED]:
        found[str(p.relative_to(ROOT))] = sorted(set(TOKEN.findall(p.read_text(encoding="utf-8"))))
    return found


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--twins", default=None, help="the vtf twin register output (default: the main checkout's out/twins_real)")
    ap.add_argument("--no-render", action="store_true", help="skip the slide PNG and PDF render")
    ap.add_argument("--check", action="store_true", help="only list the placeholders left; exit 1 if any")
    args = ap.parse_args()
    if not args.check:
        n = numbers(Path(args.twins) if args.twins else None)
        filled = [k for k in ("AUDIT", "SIM2REAL", "REAL_TRAP", "TEAM_NAME", "TEAM_LEADER", "VIDEO_URL", "SLIDES_URL") if n.get(k)]
        print(f"refresh: filled {', '.join(filled) or 'nothing new'}; v1 numbers from findings.json")
        build_slides.build(n)
        if not args.no_render:
            import render_slides

            render_slides.render()
        video_kit.cmd_docs()
        for p in MARKED:
            count, left = fill_marked(p, n)
            print(f"refresh: {p.relative_to(ROOT)}: {count} marked spans" + (f"; still placeholders: {', '.join(left)}" if left else ""))
    left = {k: v for k, v in placeholders_left().items() if v}
    if left:
        print("placeholders left: " + "; ".join(f"{k}: {', '.join(v)}" for k, v in left.items()))
    else:
        print("placeholders left: none")
    if args.check and left:
        sys.exit(1)


if __name__ == "__main__":
    main()
