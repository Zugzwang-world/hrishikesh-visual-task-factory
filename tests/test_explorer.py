"""Task Explorer export on the fixture run: files, tolerance of v0.1 fields, model answers."""

import json
import shutil
from pathlib import Path

from PIL import Image
from typer.testing import CliRunner

from vtf.cli import app
from vtf.core import layout
from vtf.core.io import read_jsonl
from vtf.package.explorer import export_explorer, key_evidence, select_scenes

FIXTURE = Path(__file__).resolve().parents[1] / "spec" / "examples" / "run_example"
MODEL = "claude-sonnet-5-5"


def _run(tmp_path: Path) -> Path:
    run = tmp_path / "run"
    shutil.copytree(FIXTURE, run)
    rows = list(read_jsonl(run / layout.TASK_MANIFEST))
    # One model answer per task, the first one wrong; one blind answer.
    records = []
    for i, row in enumerate(rows):
        task = json.loads((run / row["path"]).read_text())
        records.append(
            {"task_id": row["task_id"], "model": MODEL, "with_image": True,
             "parsed": "wrong" if i == 0 else task["answer"], "parse_ok": True,
             "correct": i != 0, "raw_response": "{}"}
        )  # fmt: skip
    records.append(
        {"task_id": rows[0]["task_id"], "model": MODEL, "with_image": False, "parsed": None,
         "parse_ok": False, "correct": False, "raw_response": "cannot tell"}
    )  # fmt: skip
    eval_dir = run / layout.EVAL_DIR / MODEL
    eval_dir.mkdir(parents=True)
    (eval_dir / layout.EVAL_RESULTS).write_text("".join(json.dumps(r) + "\n" for r in records))
    # A v0.1-style task: no rig, no shortcut_conflict, no question_alt.
    task_path = run / rows[0]["path"]
    task = json.loads(task_path.read_text())
    for key in ("rig", "shortcut_conflict", "question_alt", "shortcut_answer", "twin_id"):
        task.pop(key, None)
    task_path.write_text(json.dumps(task))
    return run


def test_export_explorer(tmp_path: Path) -> None:
    run = _run(tmp_path)
    render = run / "scenes" / "scene_0000" / "render"
    (render / layout.GLTF).write_bytes(b"glTF-fixture")
    out = tmp_path / "data"
    index = export_explorer(run, out, n_scenes=6, max_width=200)

    assert index["source"]["n_scenes"] == 1
    assert [s["scene_id"] for s in index["scenes"]] == ["scene_0000"]
    assert index["bytes"] < 25 * 1024 * 1024
    labels = {m["label"]: m for m in index["models"]}
    assert labels[MODEL]["n"] == 3 and labels[MODEL]["correct"] == 2
    assert labels[f"{MODEL} (blind)"]["n"] == 1

    scene = json.loads((out / "scene_0000" / "scene.json").read_text())
    assert scene["gltf"] == "scene.glb" and (out / "scene_0000" / "scene.glb").exists()
    cam = scene["cameras"][0]
    assert cam["camera_id"] == "front" and cam["rig"] == "front"
    assert len(cam["K"]) == 3 and len(cam["world_to_camera"]) == 4
    with Image.open(out / "scene_0000" / cam["image"]) as img:
        assert img.format == "JPEG" and img.width == 200 == cam["image_size"][0]
    assert (out / "scene_0000" / "thumb.jpg").exists()

    obj = scene["objects"][0]
    for key in ("id", "name", "category", "color_name", "position", "rotation", "dimensions"):
        assert obj[key] is not None
    assert obj["pass_index"] >= 1

    tasks = {t["task_id"]: t for t in scene["tasks"]}
    assert len(tasks) == 3
    first = next(iter(tasks.values()))
    assert first["shortcut_conflict"] is None and first["question_alt"] is None
    assert first["rig"] == "front"  # from the manifest row, then the camera
    assert {"G1", "G3", "G7"} <= set(first["gates"])
    assert first["gates"]["G1"]["pass"] is True
    answers = {m["label"]: m for m in first["models"]}
    assert answers[MODEL]["correct"] is False and answers[MODEL]["answer"] == "wrong"
    assert answers[f"{MODEL} (blind)"]["answer"] == "cannot tell"

    # Deterministic: a second export gives the same bytes.
    before = {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}
    export_explorer(run, out, n_scenes=6, max_width=200)
    after = {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}
    assert before == after


def test_export_cli_and_refuses_foreign_folder(tmp_path: Path) -> None:
    run = _run(tmp_path)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "keep.txt").write_text("x")
    args = ["export", str(run), "--format", "explorer", "--out"]
    res = CliRunner().invoke(app, [*args, str(foreign)])
    assert res.exit_code != 0 and (foreign / "keep.txt").exists()
    out = tmp_path / "data"
    res = CliRunner().invoke(app, [*args, str(out)])
    assert res.exit_code == 0, res.output
    assert (out / "index.json").exists()


def test_select_scenes_covers_families_first() -> None:
    tasks = {
        "scene_a": [{"task_id": "a1", "family": "count"}, {"task_id": "a2", "family": "count"}],
        "scene_b": [{"task_id": "b1", "family": "count"}, {"task_id": "b2", "family": "relation"}],
        "scene_c": [{"task_id": "c1", "family": "support"}],
    }
    assert select_scenes(tasks, {}, 2) == ["scene_b", "scene_c"]


def test_key_evidence_puts_decision_first() -> None:
    evidence = {
        "program": "front_to_back",
        "answer": "red box",
        "rerun_matches": True,
        "trace": [{"step": "camera"}, {"step": "decision", "gap_m": 0.1614, "closer": "obj_02"}],
        "scene_sha256": "a" * 64,
    }
    key = key_evidence(evidence)
    assert key[0] == ["gap_m", "0.1614"] and key[1] == ["closer", "obj_02"]
    assert ["rerun_matches", "yes"] in key
    assert ["scene_sha256", "aaaaaaaaaaaa…"] in key
    assert all(k != "program" for k, _ in key)
