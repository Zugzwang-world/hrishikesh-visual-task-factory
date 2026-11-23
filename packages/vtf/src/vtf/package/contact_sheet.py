"""Contact sheet: a grid of task images, each with its question, true answer, model answer and a
tick or cross. For DEMO.md and the first end-to-end run (P09)."""

import json
import textwrap
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from vtf.core import layout
from vtf.core.io import read_jsonl, read_task

THUMB_W = 384
TEXT_H = 118
PAD = 10
BG = (246, 245, 242)
INK = (30, 30, 30)
MUTED = (110, 110, 110)
GOOD = (30, 140, 60)
BAD = (200, 40, 40)


def _font(size: int) -> ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def _pick(rows: list[dict], max_tasks: int) -> list[dict]:
    """Round-robin across families so every family shows up."""
    by_family: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_family[row["family"]].append(row)
    picked, queues = [], [list(v) for _, v in sorted(by_family.items())]
    while len(picked) < max_tasks and any(queues):
        for q in queues:
            if q and len(picked) < max_tasks:
                picked.append(q.pop(0))
    return picked


def _mark(draw: ImageDraw.ImageDraw, x: int, y: int, ok: bool | None, r: int = 13) -> None:
    if ok is None:
        draw.ellipse((x - r, y - r, x + r, y + r), outline=MUTED, width=2)
        return
    colour = GOOD if ok else BAD
    draw.ellipse((x - r, y - r, x + r, y + r), fill=colour)
    if ok:
        draw.line((x - 6, y, x - 2, y + 5, x + 7, y - 6), fill="white", width=3, joint="curve")
    else:
        draw.line((x - 6, y - 6, x + 6, y + 6), fill="white", width=3)
        draw.line((x - 6, y + 6, x + 6, y - 6), fill="white", width=3)


def make_contact_sheet(
    run_dir: Path, model: str, out: Path, max_tasks: int = 24, cols: int = 4, title: str = ""
) -> dict:
    run_dir = Path(run_dir)
    lay = layout.RunLayout(run_dir)
    results_path = lay.eval_dir(model) / layout.EVAL_RESULTS
    results = {r["task_id"]: r for r in read_jsonl(results_path)} if results_path.exists() else {}
    rows = [r for r in read_jsonl(lay.task_manifest) if not results or r["task_id"] in results]
    picked = _pick(rows, max_tasks)
    if not picked:
        raise SystemExit(f"no tasks to show in {run_dir}")

    sample = Image.open(lay.task_dir(picked[0]["task_id"]) / layout.TASK_IMAGE)
    thumb_h = round(sample.height * THUMB_W / sample.width)
    rows_n = (len(picked) + cols - 1) // cols
    header_h = 64
    W = cols * THUMB_W + (cols + 1) * PAD
    H = header_h + rows_n * (thumb_h + TEXT_H + PAD) + PAD
    sheet = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(sheet)

    correct = sum(1 for r in picked if results.get(r["task_id"], {}).get("correct"))
    scored = sum(1 for r in picked if r["task_id"] in results)
    head = title or "Visual Task Factory: first end-to-end run"
    draw.text((PAD, 12), head, fill=INK, font=_font(24))
    sub = f"model {model}: {correct}/{scored} correct on the tasks shown" if scored else ""
    draw.text((PAD, 40), sub, fill=MUTED, font=_font(15))

    f_q, f_a = _font(15), _font(15)
    for i, row in enumerate(picked):
        task = read_task(run_dir / row["path"])
        x = PAD + (i % cols) * (THUMB_W + PAD)
        y = header_h + (i // cols) * (thumb_h + TEXT_H + PAD)
        img = Image.open(lay.task_dir(task.task_id) / task.image).convert("RGB")
        sheet.paste(img.resize((THUMB_W, thumb_h), Image.LANCZOS), (x, y))
        ty = y + thumb_h + 6
        for line in textwrap.wrap(task.question, width=44)[:2]:
            draw.text((x, ty), line, fill=INK, font=f_q)
            ty += 19
        res = results.get(task.task_id)
        draw.text((x, ty + 4), f"Truth: {task.answer_text}", fill=INK, font=f_a)
        if res is None:
            model_text, ok = "Model: (not evaluated)", None
        else:
            shown = res["parsed"] if res["parse_ok"] else (res["raw_response"] or res.get("error"))
            model_text = f"Model: {json.dumps(shown) if isinstance(shown, list) else shown}"
            ok = bool(res["correct"])
        draw.text((x, ty + 26), textwrap.shorten(str(model_text), 42), fill=INK, font=f_a)
        draw.text((x, ty + 48), f"{task.family}  {task.task_id}", fill=MUTED, font=_font(12))
        _mark(draw, x + THUMB_W - 20, ty + 34, ok)

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, optimize=True)
    print(f"contact sheet: {out} ({len(picked)} tasks, {correct}/{scored} correct)")
    return {"path": str(out), "tasks": len(picked), "correct": correct, "scored": scored}
