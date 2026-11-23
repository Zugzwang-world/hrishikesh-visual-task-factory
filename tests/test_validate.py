"""Validate pipeline on the fixture run: gates, packaging, rejection log, contact sheet."""

import json
import shutil
import sys
import types
from pathlib import Path

import pytest

from vtf.core import layout
from vtf.core.io import read_jsonl, read_manifest, read_task, write_model
from vtf.core.program import CompiledQuestion, ProgramRef, ProgramResult
from vtf.core.projection import to_camera
from vtf.validate.gates import g2_second_computation as g2
from vtf.validate.gates import g3_evidence_visible as g3

FIXTURE = Path(__file__).resolve().parents[1] / "spec" / "examples" / "run_example"


def _stub_programs() -> types.ModuleType:
    """A tiny stand-in for vtf.programs, so these tests pin the pipeline, not the programs."""
    m = types.ModuleType("vtf.programs")
    m.PROGRAMS_VERSION = "stub"

    def run_program(name, params, scene, camera_id):
        if name == "count_all":
            ids = [o.id for o in scene.objects]
            return ProgramResult(
                answer=len(ids), answer_type="int", answer_text=str(len(ids)),
                evidence_object_ids=ids,
            )  # fmt: skip
        a, b = scene.object(params["a"]), scene.object(params["b"])
        cam = scene.camera(camera_id)
        za, zb = (to_camera(cam, [o.pose.position])[0, 2] for o in (a, b))
        c = a if za < zb else b
        return ProgramResult(
            answer=c.name, answer_type="object", answer_text=c.name,
            evidence_object_ids=[a.id, b.id], margin=abs(za - zb),
        )  # fmt: skip

    def compile_questions(scene, camera_id, seed, max_questions=5):
        specs = [
            ("count_all", {}, "count"),
            ("front_to_back", {"a": "obj_06", "b": "obj_01"}, "front_to_back"),
        ]
        out = [
            CompiledQuestion(
                family=f,
                template_id=f"{n}.p1",
                question=f"{n}?",
                camera_id=camera_id,
                program=ProgramRef(name=n, params=p),
                result=run_program(n, p, scene, camera_id),
            )  # fmt: skip
            for n, p, f in specs
        ]
        out.append(
            CompiledQuestion(
                family="nearest_to_gripper",
                template_id="n.p1",
                question="n?",
                camera_id=camera_id,
                program=ProgramRef(name="nearest_to_gripper"),
                rejected="near_tie_gripper",
            )  # fmt: skip
        )
        return out

    m.run_program, m.compile_questions = run_program, compile_questions
    return m


def _small_fixture(monkeypatch) -> None:
    """The fixture camera is only 320x240: scale the pixel thresholds down with it."""
    monkeypatch.setattr(g3, "MIN_VISIBLE_PIXELS", 100)
    monkeypatch.setattr(g3, "COUNT_MIN_VISIBLE_PIXELS", 60)
    monkeypatch.setattr(g2, "COUNT_MIN_PIXELS", 60)


@pytest.fixture
def run(tmp_path, monkeypatch) -> Path:
    monkeypatch.setitem(sys.modules, "vtf.programs", _stub_programs())
    dst = tmp_path / "run"
    shutil.copytree(FIXTURE, dst)
    shutil.rmtree(dst / layout.TASKS_DIR)
    (dst / layout.TASK_MANIFEST).unlink()
    return dst


def test_validate_packages_and_rejects(run, monkeypatch) -> None:
    from vtf.validate.pipeline import validate_run

    _small_fixture(monkeypatch)
    report = validate_run(run)
    assert report["accepted"] == 2
    assert report["rejection_reasons"] == {"compile:near_tie_gripper": 1}
    beauty = layout.RunLayout(run).render_dir("scene_0000") / "front" / layout.BEAUTY
    for row in read_jsonl(run / layout.TASK_MANIFEST):
        assert row["rig"] == "front"
        task = read_task(run / row["path"])
        assert set(task.gates) == {"G1", "G2", "G3", "G4", "G6", "G7"}
        assert all(g.passed for g in task.gates.values())
        assert task.gates["G2"].evidence["agree"] is True
        assert task.gates["G6"].evidence == {"status": "not_sampled"}
        assert {o.id for o in task.scene_objects} == {f"obj_0{i}" for i in range(7)}
        assert task.difficulty["rig"] == "front" and "min_visible_fraction" in task.difficulty
        image = run / layout.TASKS_DIR / task.task_id / layout.TASK_IMAGE
        assert image.stat().st_ino == beauty.stat().st_ino  # hard link, no copy
    # Same inputs, same task ids.
    first = sorted(r["task_id"] for r in read_jsonl(run / layout.TASK_MANIFEST))
    validate_run(run)
    assert sorted(r["task_id"] for r in read_jsonl(run / layout.TASK_MANIFEST)) == first


def test_incremental_skip_collect_and_revalidate(run, monkeypatch, capsys) -> None:
    from vtf.validate import pipeline

    _small_fixture(monkeypatch)
    lay = layout.RunLayout(run)
    assert pipeline.validate_scene(lay, "scene_0000") == "validated"
    assert pipeline.validate_scene(lay, "scene_0000") == "skipped"
    first = pipeline.collect(run)
    manifest = (run / layout.TASK_MANIFEST).read_text()
    # collect alone rebuilds the run files from the per-scene results
    (run / layout.TASK_MANIFEST).unlink()
    (run / layout.REJECTED).unlink()
    again = pipeline.collect(run)
    assert (run / layout.TASK_MANIFEST).read_text() == manifest
    assert again["accepted"] == first["accepted"] == 2
    # an orphan task folder is pruned
    (lay.tasks_dir / "t_orphan").mkdir()
    pipeline.collect(run)
    assert not (lay.tasks_dir / "t_orphan").exists()
    # a changed render invalidates the validation key: the scene is validated again
    mpath = lay.render_manifest("scene_0000")
    manifest_model = read_manifest(mpath)
    manifest_model.timings["total"] = 1.0
    write_model(mpath, manifest_model)
    assert pipeline.validate_scene(lay, "scene_0000") == "validated"
    # a missing task folder also forces a redo
    row = next(read_jsonl(run / layout.TASK_MANIFEST))
    import shutil as sh

    sh.rmtree(run / row["path"].rsplit("/", 1)[0])
    assert pipeline.validate_scene(lay, "scene_0000") == "validated"
    assert (run / row["path"]).exists()


def test_unrendered_scene_waits(run, monkeypatch) -> None:
    from vtf.validate import pipeline

    lay = layout.RunLayout(run)
    shutil.copytree(lay.scene_dir("scene_0000"), lay.scene_dir("scene_0001"))
    shutil.rmtree(lay.render_dir("scene_0001"))
    assert pipeline.validate_scene(lay, "scene_0001") == "not_rendered"
    report = pipeline.collect(run)
    assert report["scene_status"]["not_rendered"] == 1


def test_g3_rejects_hidden_evidence(run) -> None:
    from vtf.validate.pipeline import validate_run

    report = validate_run(run)  # default 300 px: small 320x240 objects fail rule A
    assert report["gates"]["G3"]["pass"] < report["gates"]["G3"]["run"]
    reasons = [r["reason"] for r in read_jsonl(run / layout.REJECTED)]
    assert "evidence_not_visible" in reasons


def test_unsettled_scene_is_rejected(run) -> None:
    from vtf.validate.pipeline import validate_run

    mpath = layout.RunLayout(run).render_manifest("scene_0000")
    manifest = read_manifest(mpath)
    manifest.settle.moving_at_end = ["obj_02"]
    write_model(mpath, manifest)
    report = validate_run(run)
    assert report["accepted"] == 0
    assert report["rejection_reasons"] == {"settle:settle_moving_at_end": 1}


def test_contact_sheet(run, monkeypatch) -> None:
    from vtf.package.contact_sheet import make_contact_sheet
    from vtf.validate.pipeline import validate_run

    _small_fixture(monkeypatch)
    validate_run(run)
    rows = list(read_jsonl(run / layout.TASK_MANIFEST))
    edir = layout.RunLayout(run).eval_dir("fake")
    edir.mkdir(parents=True)
    with open(edir / layout.EVAL_RESULTS, "w") as f:
        for r in rows:
            answer = read_task(run / r["path"]).answer
            f.write(json.dumps({"task_id": r["task_id"], "parse_ok": True, "parsed": answer,
                                "correct": True, "raw_response": ""}) + "\n")  # fmt: skip
    out = make_contact_sheet(run, "fake", run / "sheet.png")
    assert out["correct"] == out["scored"] == len(rows)
    assert (run / "sheet.png").stat().st_size > 10_000
