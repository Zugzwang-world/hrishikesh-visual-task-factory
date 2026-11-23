"""Task Explorer export on the fixture run: files, tolerance of v0.1 fields, model answers."""

import json
import shutil
from pathlib import Path

from PIL import Image
from typer.testing import CliRunner

from vtf.cli import app
from vtf.core import layout
from vtf.core.io import read_jsonl
from vtf.package.explorer import (
    _Named,
    compress_glb,
    export_explorer,
    highlight_ids,
    key_evidence,
    model_display_name,
    named_ids,
    project_uv,
    select_scenes,
)

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
    assert key[0] == ["gap m", "0.1614"] and key[1] == ["closer", "obj_02"]
    assert ["rerun matches", "yes"] in key
    assert ["scene sha256", "aaaaaaaaaaaa…"] in key
    assert all(k != "program" for k, _ in key)


# ---------------------------------------------------------------- v0.2: results, gallery, ids

F2B = "tb9c7f3a0d6fbba42"  # fixture front_to_back: yellow cone (obj_03) vs blue cylinder (obj_01)
OBJECTS = [
    _Named("obj_00", "box", "red", None),
    _Named("obj_01", "cylinder", "blue", None),
    _Named("obj_03", "cone", "yellow", None),
    _Named("obj_04", "cylinder", "red", None),
    _Named("obj_05", "cylinder", "white", None),
]


def _conflict_run(tmp_path: Path) -> Path:
    """The fixture run with its front_to_back task tagged as a 2D-shortcut conflict, and three
    sweep-style result folders (Opus xhigh, Haiku, a blind baseline) beside it."""
    run = tmp_path / "run"
    shutil.copytree(FIXTURE, run)
    task_path = run / "tasks" / F2B / "task.json"
    task = json.loads(task_path.read_text())
    task.update(shortcut_conflict=True, shortcut_answer="blue cylinder")
    task_path.write_text(json.dumps(task))
    rows = list(read_jsonl(run / layout.TASK_MANIFEST))
    for key, model_id, effort, blind, answer in (
        ("claude-opus-5-5-xhigh", "claude-opus-5-5", "xhigh", False, "yellow cone"),
        ("claude-haiku-5-5", "claude-haiku-5-5", "high", False, "blue cylinder"),
        ("claude-sonnet-5-5__blind", "claude-sonnet-5-5", "high", True, "red box"),
    ):
        folder = tmp_path / "sweep" / key
        folder.mkdir(parents=True)
        recs = [
            {"task_id": r["task_id"], "model": key, "model_id": model_id, "effort": effort,
             "with_image": not blind, "parse_ok": True,
             "parsed": answer if r["task_id"] == F2B else 0,
             "correct": r["task_id"] == F2B and answer == "yellow cone", "raw_response": "{}"}
            for r in rows
        ]  # fmt: skip
        (folder / layout.EVAL_RESULTS).write_text("".join(json.dumps(r) + "\n" for r in recs))
        summary = {"model": key, "model_id": model_id, "with_image": not blind,
                   "settings": {"effort": effort}}  # fmt: skip
        (folder / layout.EVAL_SUMMARY).write_text(json.dumps(summary))
    return run


def test_results_folders_label_order_and_display(tmp_path: Path) -> None:
    run = _conflict_run(tmp_path)
    sweep = tmp_path / "sweep"
    dirs = [sweep / "claude-sonnet-5-5__blind", sweep / "claude-opus-5-5-xhigh",
            sweep / "claude-haiku-5-5", sweep / "missing"]  # fmt: skip
    index = export_explorer(run, tmp_path / "data", results_dirs=dirs)

    models = index["models"]
    assert [m["label"] for m in models] == [
        "claude-haiku-5-5", "claude-opus-5-5-xhigh", "claude-sonnet-5-5__blind",
    ]  # fmt: skip
    assert [m["display_name"] for m in models] == [
        "Haiku 5.5 · high", "Opus 5.5 · xhigh", "Sonnet 5.5 · high · blind",
    ]  # fmt: skip
    assert [m["with_image"] for m in models] == [True, True, False]
    assert models[1]["effort"] == "xhigh" and models[1]["n"] == 3 and models[1]["correct"] == 1

    scene = json.loads((tmp_path / "data" / "scene_0000" / "scene.json").read_text())
    task = next(t for t in scene["tasks"] if t["task_id"] == F2B)
    answers = {m["label"]: m for m in task["models"]}
    assert list(answers) == [m["label"] for m in models]
    assert answers["claude-opus-5-5-xhigh"]["display_name"] == "Opus 5.5 · xhigh"
    assert answers["claude-opus-5-5-xhigh"]["correct"] is True
    assert answers["claude-opus-5-5-xhigh"]["answer_object_ids"] == ["obj_03"]
    assert answers["claude-opus-5-5-xhigh"]["took_shortcut"] is False
    assert answers["claude-haiku-5-5"]["took_shortcut"] is True
    assert answers["claude-sonnet-5-5__blind"]["took_shortcut"] is False
    assert answers["claude-sonnet-5-5__blind"]["answer_object_ids"] == ["obj_00"]


def test_gallery_export(tmp_path: Path) -> None:
    run = _conflict_run(tmp_path)
    gallery = tmp_path / "findings.json"
    item = {"task_id": F2B, "scene_id": "scene_0000", "family": "front_to_back",
            "camera": "front", "question": "Which is closer?", "truth": "yellow cone",
            "shortcut_answer": "blue cylinder",
            "models_taking_shortcut": ["claude-haiku-5-5"]}  # fmt: skip
    missing = {**item, "task_id": "t_not_in_run"}
    gallery.write_text(json.dumps({"gallery": [item, missing]}))
    out = tmp_path / "data"
    dirs = [tmp_path / "sweep" / "claude-haiku-5-5", tmp_path / "sweep" / "claude-opus-5-5-xhigh"]
    # No coverage scenes: the gallery scene is exported anyway.
    index = export_explorer(run, out, n_scenes=0, results_dirs=dirs, gallery=gallery)

    assert [(s["scene_id"], s["gallery"]) for s in index["scenes"]] == [("scene_0000", True)]
    skipped = [{"task_id": "t_not_in_run", "reason": "not an accepted task"}]
    assert index["gallery_skipped"] == skipped
    (entry,) = index["gallery"]
    assert entry["task_id"] == F2B and entry["camera"] == "front" and entry["task_index"] == 2
    assert entry["truth_object_ids"] == ["obj_03"] and entry["shortcut_object_ids"] == ["obj_01"]
    assert entry["models_taking_shortcut"] == ["claude-haiku-5-5"]
    assert entry["thumb"] == "scene_0000/thumb_front.jpg" and (out / entry["thumb"]).exists()
    roles = {m["object_id"]: m["role"] for m in entry["marks"]}
    assert roles == {"obj_03": "truth", "obj_01": "shortcut"}
    assert all(0 <= x <= 1 for m in entry["marks"] for x in m["uv"])
    took = {m["label"]: m["took_shortcut"] for m in entry["models"]}
    assert took == {"claude-haiku-5-5": True, "claude-opus-5-5-xhigh": False}

    scene = json.loads((out / "scene_0000" / "scene.json").read_text())
    tasks = {t["task_id"]: t for t in scene["tasks"]}
    assert tasks[F2B]["truth_object_ids"] == ["obj_03"]
    assert tasks[F2B]["shortcut_object_ids"] == ["obj_01"]
    assert all("truth_object_ids" not in t for tid, t in tasks.items() if tid != F2B)


def test_highlight_ids_naming_rule_trace_and_bool() -> None:
    task = {"answer_type": "object", "answer": "cone", "shortcut_answer": "blue cylinder"}
    assert highlight_ids(task, OBJECTS) == (["obj_03"], ["obj_01"])
    # 'cylinder' names three objects: the program's G1 trace says which one the 2D rule picked.
    trace = [{"step": "decision"}, {"step": "shortcut", "object_id": "obj_05"}]
    task = {"answer_type": "object", "answer": "yellow cone", "shortcut_answer": "cylinder",
            "gates": {"G1": {"evidence": {"trace": trace}}}}  # fmt: skip
    assert highlight_ids(task, OBJECTS) == (["obj_03"], ["obj_05"])
    # A name that names nothing, and no trace: no shortcut object.
    task = {"answer_type": "object", "answer": "red box", "shortcut_answer": "green mug"}
    assert highlight_ids(task, OBJECTS) == (["obj_00"], [])
    # Sets: every name; an empty shortcut set marks nothing.
    task = {"answer_type": "set", "answer": ["red box", "white cylinder"], "shortcut_answer": []}
    assert highlight_ids(task, OBJECTS) == (["obj_00", "obj_05"], [])
    # Bool (free space): the evidence objects carry the true answer.
    task = {"answer_type": "bool", "answer": False, "shortcut_answer": True,
            "evidence_object_ids": ["obj_04", "obj_00", "obj_01"]}  # fmt: skip
    assert highlight_ids(task, OBJECTS) == (["obj_04", "obj_00", "obj_01"], [])
    assert named_ids("table", OBJECTS) == [] and named_ids("cylinder", OBJECTS) is None


def test_model_display_name() -> None:
    assert model_display_name("claude-opus-5-5", "xhigh") == "Opus 5.5 · xhigh"
    blind = model_display_name("claude-sonnet-5-5__blind", "high", False)
    assert blind == "Sonnet 5.5 · high · blind"
    assert model_display_name("claude-haiku-4-5-20251001", None) == "Haiku 4.5"
    assert model_display_name("gpt-x", None) == "gpt-x"


def test_project_uv_centre_and_behind() -> None:
    cam = {"world_to_camera": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
           "K": [[100, 0, 50], [0, 100, 40], [0, 0, 1]], "resolution": [100, 80]}  # fmt: skip
    assert project_uv(cam, [0, 0, 2]) == [0.5, 0.5]
    assert project_uv(cam, [0, 0, -2]) is None
    assert project_uv(cam, [5, 0, 1]) is None  # off-frame
    plain = project_uv(cam, [0.2, 0.1, 1])
    barrel = project_uv({**cam, "quality": {"distortion_k1": -0.15}}, [0.2, 0.1, 1])
    assert plain == [0.7, 0.625] and barrel is not None and barrel != plain


def test_glb_compression_falls_back_to_plain_copy(tmp_path: Path) -> None:
    run = _run(tmp_path)
    render = run / "scenes" / "scene_0000" / "render"
    (render / layout.GLTF).write_bytes(b"glTF-fixture")  # not a real glb: compression fails
    out = tmp_path / "data"
    for script in (tmp_path / "missing.mjs", None):  # no script; the real one on a bad file
        index = export_explorer(run, out, compress_glbs=True, glb_script=script)
        summary = index["scenes"][0]
        assert summary["gltf"] is True and summary["gltf_compressed"] is False
        assert (out / "scene_0000" / layout.GLTF).read_bytes() == b"glTF-fixture"
    assert compress_glb(render / layout.GLTF, tmp_path / "x.glb", tmp_path / "missing.mjs")
