"""P16 blind audit: a static labelling page for the founder, and its scorer.

`vtf audit make <run> --n 150` draws a seeded sample stratified by family x rig x
shortcut_conflict and writes <run>/audit/index.html, one self-contained page with the images
next to it in audit/img/. It shows one task at a time (image and question, never the answer),
keeps the answers in the browser's localStorage, and exports them as a JSON download (import
too). It works offline from file:// on a laptop or a phone.

`vtf audit score <run> answers.json` scores the export with the eval scorer and prints accuracy
per family and every miss. "Can't tell" with no answer counts as a miss: the task may not be
answerable from the image, which is exactly what the audit looks for.
"""

import json
import shutil
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup
from PIL import Image

from vtf.core.hashing import sha256_json, short_id
from vtf.core.io import read_task
from vtf.core.layout import TASK_FILE, RunLayout
from vtf.core.rng import derive_seed, make_rng
from vtf.core.task import TaskPackage

AUDIT_VERSION = "0.1.0"
SAMPLE_FILE = "sample.json"
SCORE_FILE = "score.json"
PAGE = "index.html"
IMG_DIR = "img"
DEFAULT_STRATA = ("family", "rig", "shortcut_conflict")
TARGET_ACCURACY = 0.98
JPEG_QUALITY = 90
TEMPLATES = Path(__file__).parent / "templates"


def template_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def script_json(data: Any) -> Markup:
    """JSON safe to embed in a <script type="application/json"> element."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    for char, escaped in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026")):
        text = text.replace(char, escaped)
    return Markup(text)


def _scene_names(lay: RunLayout, scene_id: str) -> set[str]:
    """Object names from the settled scene file, for v0.1 tasks that carry no scene_objects."""
    path = lay.settled_scene(scene_id)
    if not path.is_file():
        return set()
    objects = json.loads(path.read_text(encoding="utf-8")).get("objects", [])
    return {
        " ".join(w for w in (o.get("size_word"), o.get("color_name"), o.get("category")) if w)
        for o in objects
    }


def _options(task: TaskPackage, lay: RunLayout) -> list[str]:
    """Names to tap for object and set answers: every scene object, alphabetical (so the order
    says nothing about the answer). Free text is always offered as well."""
    if task.answer_type not in ("object", "set"):
        return []
    names = {o.name for o in task.scene_objects} or _scene_names(lay, task.scene_id)
    if task.family == "support" and names:
        names.add("table")
    return sorted(n for n in names if n)


def _page_item(task: TaskPackage, image: str, lay: RunLayout) -> dict[str, Any]:
    """What the page may know about a task: never the answer."""
    return {
        "id": task.task_id,
        "img": image,
        "q": task.question,
        "type": task.answer_type,
        "family": task.family,
        "rig": task.rig,
        "camera": task.camera_id,
        "options": _options(task, lay),
        "unit": "cm" if task.answer_type == "number" else None,
    }


def _copy_image(src: Path, out_dir: Path, task_id: str, image_format: str) -> str:
    img_dir = out_dir / IMG_DIR
    img_dir.mkdir(parents=True, exist_ok=True)
    if image_format == "png":
        dst = img_dir / f"{task_id}.png"
        shutil.copyfile(src, dst)
    else:
        dst = img_dir / f"{task_id}.jpg"
        with Image.open(src) as im:
            im.convert("RGB").save(dst, "JPEG", quality=JPEG_QUALITY, optimize=True)
    return f"{IMG_DIR}/{dst.name}"


def make_audit(
    run_dir: Path | str,
    n: int | None = 150,
    seed: int = 0,
    *,
    out_dir: Path | str | None = None,
    families: Iterable[str] | None = None,
    strata: Sequence[str] = DEFAULT_STRATA,
    image_format: str = "jpg",
    title: str | None = None,
    echo: Callable[[str], Any] = print,
) -> Path:
    """Write the blind audit page for a stratified sample of `n` accepted tasks (all when None)."""
    from vtf.eval.runner import manifest_rows, stratified_sample

    run_dir = Path(run_dir)
    lay = RunLayout(run_dir)
    out = Path(out_dir) if out_dir is not None else lay.audit_dir
    rows = manifest_rows(run_dir)
    if families is not None:
        wanted = set(families)
        rows = [r for r in rows if r.family in wanted]
    if not rows:
        raise SystemExit(f"vtf audit: no accepted tasks to sample in {run_dir}")
    picked = stratified_sample(rows, n, strata, seed) if n is not None else rows
    order = make_rng(derive_seed(seed, "audit-order")).permutation(len(picked))
    picked = [picked[int(i)] for i in order]

    page, strata_counts = _write_page(
        run_dir, picked, out, seed=seed, strata=strata, image_format=image_format, title=title
    )
    families_n = Counter(r.family for r in picked)
    echo(
        f"vtf audit: {len(picked)} tasks from {len(rows)} accepted "
        f"({len(strata_counts)} strata of {' x '.join(strata)}) -> {page}"
    )
    echo("  " + ", ".join(f"{f} {c}" for f, c in sorted(families_n.items())))
    return page


def _write_page(
    run_dir: Path,
    picked: Sequence[Any],
    out: Path,
    *,
    seed: int,
    strata: Sequence[str],
    image_format: str,
    title: str | None,
    sample_name: str = SAMPLE_FILE,
    extra: dict[str, Any] | None = None,
) -> tuple[Path, Counter]:
    """Copy the images, write index.html and the sample record for `picked` manifest rows."""
    from vtf.eval.runner import stratum_value

    lay = RunLayout(run_dir)
    if (out / IMG_DIR).exists():
        shutil.rmtree(out / IMG_DIR)
    items = []
    for row in picked:
        task_file = run_dir / row.path
        task = read_task(task_file)
        image = _copy_image(task_file.parent / task.image, out, task.task_id, image_format)
        items.append(_page_item(task, image, lay))

    ids = [item["id"] for item in items]
    audit_id = short_id("a", sha256_json({"run": run_dir.resolve().name, "task_ids": ids}), 12)
    strata_counts = Counter("|".join(stratum_value(r, k) for k in strata) for r in picked)
    data = {
        "audit_id": audit_id,
        "audit_version": AUDIT_VERSION,
        "run": run_dir.resolve().name,
        "title": title or f"Blind audit: {run_dir.resolve().name}",
        "tasks": items,
    }
    html = (
        template_env()
        .get_template("audit.html.j2")
        .render(title=data["title"], data_json=script_json(data), n=len(items))
    )
    out.mkdir(parents=True, exist_ok=True)
    (out / PAGE).write_text(html, encoding="utf-8")
    sample = {
        "audit_id": audit_id,
        "audit_version": AUDIT_VERSION,
        "run": str(run_dir.resolve()),
        "n": len(ids),
        "seed": seed,
        "strata": list(strata),
        "strata_counts": dict(sorted(strata_counts.items())),
        **(extra or {}),
        "task_ids": ids,
    }
    (out / sample_name).write_text(json.dumps(sample, indent=2) + "\n", encoding="utf-8")
    return out / PAGE, strata_counts


def _base_scene(scene_id: str) -> str:
    return scene_id.split("_twin_")[0]


def refresh_audit(
    run_dir: Path | str,
    exclude_scenes: Iterable[str],
    *,
    seed: int = 1,
    prefer_ids: Iterable[str] | None = None,
    out_dir: Path | str | None = None,
    sample_name: str = "sample_v2.json",
    image_format: str = "jpg",
    title: str | None = None,
    echo: Callable[[str], Any] = print,
) -> Path:
    """Replace every task of the existing audit draw (<audit>/sample.json, left untouched) whose
    scene the auditor has already seen with answers, by a fresh task of the same family x rig x
    shortcut_conflict stratum from an unseen scene. Twins count as their source scene.

    Candidates in `prefer_ids` (tasks the models already answered) are taken first. If a stratum
    has no fresh task, the nearest stratum is used: same family and conflict label on another
    rig, then the same family, then anything; each replacement records its fallback level.
    Kept tasks stay in their slots, so the page order only changes where a task was replaced.
    """
    from vtf.eval.runner import manifest_rows, stratum_value

    run_dir = Path(run_dir)
    lay = RunLayout(run_dir)
    out = Path(out_dir) if out_dir is not None else lay.audit_dir
    base = json.loads((lay.audit_dir / SAMPLE_FILE).read_text(encoding="utf-8"))
    strata = tuple(base.get("strata") or DEFAULT_STRATA)
    excluded = {_base_scene(s) for s in exclude_scenes}
    prefer = set(prefer_ids or ())
    rows = manifest_rows(run_dir)
    by_id = {r.task_id: r for r in rows}
    used = set(base["task_ids"])
    fresh = [r for r in rows if _base_scene(r.scene_id) not in excluded]

    def key(r: Any, ks: Sequence[str]) -> tuple[str, ...]:
        return tuple(stratum_value(r, k) for k in ks)

    levels = (
        ("same_stratum", strata),
        ("same_family_and_conflict", ("family", "shortcut_conflict")),
        ("same_family", ("family",)),
        ("any", ()),
    )
    picked, replacements = [], []
    for task_id in base["task_ids"]:
        row = by_id[task_id]
        if _base_scene(row.scene_id) not in excluded:
            picked.append(row)
            continue
        level = ""
        for name, ks in levels:
            cands = sorted(
                (c for c in fresh if c.task_id not in used and key(c, ks) == key(row, ks)),
                key=lambda c: (c.task_id not in prefer, c.task_id),
            )
            if cands:
                level = name
                break
        else:
            raise SystemExit(f"vtf audit refresh: no fresh task left to replace {task_id}")
        top = [c for c in cands if (c.task_id in prefer) == (cands[0].task_id in prefer)]
        rng = make_rng(derive_seed(seed, "audit-refresh", task_id))
        new = top[int(rng.integers(len(top)))]
        used.add(new.task_id)
        picked.append(new)
        replacements.append(
            {
                "old": task_id,
                "old_scene": row.scene_id,
                "new": new.task_id,
                "new_scene": new.scene_id,
                "stratum": "|".join(key(row, strata)),
                "level": level,
                "models_answered": new.task_id in prefer,
            }
        )

    extra = {
        "refreshed_from": base["audit_id"],
        "excluded_scenes": sorted(excluded),
        "replacements": replacements,
    }
    page, strata_counts = _write_page(
        run_dir,
        picked,
        out,
        seed=seed,
        strata=strata,
        image_format=image_format,
        title=title or f"Blind audit v2: {run_dir.resolve().name} (no Explorer scenes)",
        sample_name=sample_name,
        extra=extra,
    )
    conflict = sum(r.shortcut_conflict is True for r in picked)
    fallbacks = Counter(r["level"] for r in replacements)
    echo(
        f"vtf audit refresh: {len(replacements)} of {len(picked)} tasks replaced "
        f"({dict(fallbacks)}); {conflict} shortcut-conflict tasks -> {page}"
    )
    return page


# --- scoring ----------------------------------------------------------------------------------


def _entries(data: Any) -> dict[str, dict[str, Any]]:
    """Accept the page's export ({"answers": {id: {...}}}), a bare {id: {...}} map, or a list of
    {"task_id": ..., "answer": ...} rows."""
    answers = data.get("answers", data) if isinstance(data, dict) else data
    if isinstance(answers, list):
        return {row["task_id"]: row for row in answers}
    out = {}
    for task_id, entry in answers.items():
        out[task_id] = entry if isinstance(entry, dict) else {"answer": entry}
    return out


def score_audit(
    run_dir: Path | str,
    answers_path: Path | str,
    *,
    audit_dir: Path | str | None = None,
    echo: Callable[[str], Any] = print,
) -> dict[str, Any]:
    """Score an exported answers file against the run's tasks with the eval scorer."""
    from vtf.eval.runner import score_value_for_task

    run_dir = Path(run_dir)
    lay = RunLayout(run_dir)
    audit = Path(audit_dir) if audit_dir is not None else lay.audit_dir
    data = json.loads(Path(answers_path).read_text(encoding="utf-8"))
    entries = _entries(data)
    sample = _find_sample(audit, data.get("audit_id") if isinstance(data, dict) else None)
    expected = (data.get("task_ids") if isinstance(data, dict) else None) or sample.get("task_ids")
    expected = list(expected or entries)

    rows: list[dict[str, Any]] = []
    missing_tasks = []
    for task_id in expected:
        entry = entries.get(task_id)
        if entry is None:
            continue
        task_file = lay.task_dir(task_id) / TASK_FILE
        if not task_file.is_file():
            missing_tasks.append(task_id)
            continue
        task = read_task(task_file)
        value = entry.get("answer")
        flag = bool(entry.get("flag"))
        row = {
            "task_id": task_id,
            "family": task.family,
            "rig": task.rig,
            "shortcut_conflict": task.shortcut_conflict,
            "question": task.question,
            "truth": task.answer_text,
            "given": value,
            "flag": flag,
            "note": entry.get("note") or "",
        }
        if value is None or value == "":
            if not flag:
                continue  # neither answered nor flagged: not audited
            row.update(status="cant_tell", parsed=None, correct=False)
        else:
            try:
                score = score_value_for_task(task, value)
                row.update(
                    status="scored" if score.parse_ok else "unparseable",
                    parsed=score.parsed,
                    correct=score.correct,
                )
            except (KeyError, ValueError, TypeError) as exc:
                row.update(status="unscorable", parsed=None, correct=False, error=str(exc))
        rows.append(row)

    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_family[row["family"]].append(row)
    correct = sum(r["correct"] for r in rows)
    result = {
        "audit_id": data.get("audit_id") if isinstance(data, dict) else None,
        "sample_size": len(expected),
        "audited": len(rows),
        "not_answered": len(expected) - len(rows) - len(missing_tasks),
        "missing_tasks": missing_tasks,
        "correct": correct,
        "accuracy": round(correct / len(rows), 4) if rows else None,
        "target": TARGET_ACCURACY,
        "meets_target": bool(rows) and correct / len(rows) >= TARGET_ACCURACY,
        "flagged": sum(r["flag"] for r in rows),
        "families": {
            f: {
                "n": len(rs),
                "correct": sum(r["correct"] for r in rs),
                "accuracy": round(sum(r["correct"] for r in rs) / len(rs), 4),
            }
            for f, rs in sorted(by_family.items())
        },
        "misses": [r for r in rows if not r["correct"]],
        "flags": [r for r in rows if r["flag"] and r["correct"]],
    }
    out = audit if audit.is_dir() else Path(answers_path).parent
    (out / SCORE_FILE).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    echo(format_score(result))
    echo(f"  wrote {out / SCORE_FILE}")
    return result


def _find_sample(audit: Path, audit_id: str | None) -> dict[str, Any]:
    """The sample record of this audit: the sample*.json whose audit_id matches the export,
    else sample.json."""
    if audit_id and audit.is_dir():
        for path in sorted(audit.glob("sample*.json")):
            sample = json.loads(path.read_text(encoding="utf-8"))
            if sample.get("audit_id") == audit_id:
                return sample
    path = audit / SAMPLE_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def format_score(result: dict[str, Any]) -> str:
    acc = result["accuracy"]
    head = (
        f"founder audit: {result['correct']}/{result['audited']} correct"
        + ("" if acc is None else f" ({acc:.1%})")
        + f", target {result['target']:.0%}: {'met' if result['meets_target'] else 'NOT met'}"
    )
    lines = [head]
    if result["not_answered"]:
        lines.append(f"  {result['not_answered']} of {result['sample_size']} not answered yet")
    if result["missing_tasks"]:
        lines.append(f"  {len(result['missing_tasks'])} answered task(s) not found in this run")
    fams = result["families"]
    width = max([len(f) for f in fams] + [6])
    for family, s in fams.items():
        lines.append(f"  {family:<{width}}  {s['correct']:>3}/{s['n']:<3} {s['accuracy']:6.1%}")
    if result["misses"]:
        lines.append("  misses:")
        for m in result["misses"]:
            given = "can't tell" if m["status"] == "cant_tell" else json.dumps(m["given"])
            note = f"  note: {m['note']}" if m["note"] else ""
            lines.append(
                f"    {m['task_id']} [{m['family']}, {m['rig']}] {m['question']}  "
                f"truth: {m['truth']}  founder: {given} ({m['status']}){note}"
            )
    return "\n".join(lines)
