"""Scale run end to end with a fake renderer (the analytic ray caster instead of Blender): batches,
G6 twins with a stub twin rule, progress.json, and resume after a crash without re-rendering."""

import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from vtf.core import layout
from vtf.core.geometry import Pose
from vtf.core.hashing import sha256_file
from vtf.core.io import (
    read_jsonl,
    read_scene,
    read_task,
    write_depth,
    write_index_png,
    write_mask_png,
    write_model,
    write_rgb_png,
)
from vtf.core.program import TwinEdit
from vtf.core.projection import intrinsics, world_to_camera
from vtf.core.raycast import raycast
from vtf.core.render_manifest import CameraOutputs, RenderManifest, SettleReport
from vtf.render import bridge

REPO = Path(__file__).resolve().parents[1]


class Crash(Exception):
    pass


class FakeRenderer:
    """bridge.render_scene stand-in: settles by dropping objects onto the table, ray casts."""

    def __init__(self, crash_on: str | None = None) -> None:
        self.calls: list[str] = []
        self.crash_on = crash_on

    def __call__(self, scene_file, out_dir, device="gpu", blender=None, timeout=900):
        scene_file, out_dir = Path(scene_file), Path(out_dir)
        if self.crash_on and self.crash_on in str(scene_file) and "CRASH" not in self.calls:
            self.calls.append("CRASH")
            raise Crash(str(scene_file))
        self.calls.append(str(scene_file))
        spec = read_scene(scene_file)
        top = spec.table.height
        objects = []
        moved = {}
        for o in spec.objects:
            z = top + o.dimensions[2] / 2 if o.placement.kind == "table" else o.pose.position[2]
            x, y, z0 = o.pose.position
            moved[o.id] = abs(z0 - z)
            pose = Pose(position=(x, y, round(z, 6)), rotation=o.pose.rotation)
            objects.append(o.model_copy(update={"pose": pose}))
        settled = spec.model_copy(
            update={"objects": objects, "meta": spec.meta.model_copy(update={"settled": True})}
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / layout.RENDER_MANIFEST).unlink(missing_ok=True)
        write_model(out_dir / layout.SETTLED_SCENE, settled)
        cams = []
        for cam in settled.cameras:
            rc = raycast(settled, cam.id)
            cdir = out_dir / cam.id
            rgb = (np.clip(rc.colour, 0, 1) * 255).astype(np.uint8)
            write_rgb_png(cdir / layout.BEAUTY, rgb)
            write_index_png(cdir / layout.INDEX, rc.index)
            write_depth(cdir / layout.DEPTH, rc.depth)
            amodal = {}
            for p, mask in rc.amodal.items():
                rel = f"{cam.id}/{layout.AMODAL_DIR}/{p}.png"
                write_mask_png(out_dir / rel, mask)
                amodal[str(p)] = rel
            cams.append(CameraOutputs(
                camera_id=cam.id, rig=cam.rig, resolution=cam.resolution,
                beauty=f"{cam.id}/{layout.BEAUTY}", index=f"{cam.id}/{layout.INDEX}",
                depth=f"{cam.id}/{layout.DEPTH}", amodal=amodal,
                K=tuple(tuple(float(v) for v in r) for r in intrinsics(cam)),
                world_to_camera=tuple(tuple(float(v) for v in r) for r in world_to_camera(cam)),
                render_seconds=0.0, amodal_seconds=0.0,
            ))  # fmt: skip
        manifest = RenderManifest(
            scene_id=spec.meta.scene_id, scene_file=str(scene_file),
            scene_sha256=sha256_file(scene_file), renderer="fixture", blender_version="none",
            device="CPU", samples=1, seed=0, denoise=False, cameras=cams,
            settle=SettleReport(frames=2, substeps_per_frame=1, seconds=0.0,
                                displacement_m=moved, max_displacement_m=max(moved.values())),
            timings={"total": 0.0},
        )  # fmt: skip
        write_model(out_dir / layout.RENDER_MANIFEST, manifest)
        return bridge.RenderResult(True, 0.01, out_dir / layout.RENDER_MANIFEST, None,
                                   out_dir / "render.log")  # fmt: skip

    def rendered(self) -> list[str]:
        return [c for c in self.calls if c != "CRASH"]


def stub_make_twin(name, params, scene, camera_id, seed):
    """Count twins only: remove one counted object (spec/families.md)."""
    if name == "count_all":
        victims = sorted(o.id for o in scene.objects)
    elif name == "count_color":
        victims = sorted(o.id for o in scene.objects if o.color_name == params["color"])
    else:
        return None
    return TwinEdit(object_id=victims[0], remove=True, reason="remove one counted object")


@pytest.fixture
def recipe(tmp_path) -> Path:
    data = yaml.safe_load((REPO / "recipes" / "first_scene.yaml").read_text())
    data["camera"]["resolution"] = [480, 360]
    data["objects"]["count"] = [4, 6]
    data["objects"]["size_m"] = [0.06, 0.10]
    path = tmp_path / "small.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


@pytest.mark.parametrize("crash_on", ["scenes/scene_0002/", "/twins/"])
def test_scale_run_resumes_without_rerendering(tmp_path, recipe, monkeypatch, crash_on) -> None:
    import vtf.programs
    from vtf.validate.scale import scale_run

    monkeypatch.delattr(bridge, "render_scenes", raising=False)
    monkeypatch.setattr(vtf.programs, "make_twin", stub_make_twin, raising=False)
    out = tmp_path / "run"
    args = dict(recipe=recipe, n=4, seed=3, out=out, batch=2, twin_fraction=0.5)

    first = FakeRenderer(crash_on)  # dies in batch 2's scene renders, or in the first twin render
    monkeypatch.setattr(bridge, "render_scene", first)
    with pytest.raises(Crash):
        scale_run(**args)
    lay = layout.RunLayout(out)
    if crash_on.startswith("scenes"):
        progress = json.loads((lay.logs_dir / "progress.json").read_text())
        assert progress["scenes_rendered"] == 2 and progress["batches_done"] == 1

    second = FakeRenderer()
    monkeypatch.setattr(bridge, "render_scene", second)
    report = scale_run(**args)
    done_before = set(first.rendered())
    assert not done_before & set(second.rendered()), "finished renders were redone"
    scenes = [str(lay.scene_file(s)) for s in lay.scene_ids()]
    all_renders = first.rendered() + second.rendered()
    assert sorted(c for c in all_renders if c in scenes) == sorted(scenes)  # once each
    twin_renders = [c for c in all_renders if "/twins/" in c]
    assert len(twin_renders) == len(set(twin_renders)) > 0

    progress = json.loads((lay.logs_dir / "progress.json").read_text())
    assert progress["scenes_rendered"] == 4 and progress["scenes_failed"] == 0
    assert progress["eta_seconds"] == 0 and progress["tasks_accepted"] == report["accepted"] > 0
    assert set(progress["gate_pass_rates"]) >= {"G1", "G2", "G3", "G4", "G6", "G7"}
    assert "batch 2/2 done" in (lay.logs_dir / "scale_run.log").read_text()

    # G6: twins packaged as their own tasks, linked both ways, answers differ
    rows = list(read_jsonl(lay.task_manifest))
    tasks = {r["task_id"]: read_task(out / r["path"]) for r in rows}
    twins = [t for t in tasks.values() if t.twin_of]
    assert twins and report["twin_tasks"] == len(twins)
    for twin in twins:
        original = tasks[twin.twin_of]
        assert original.twin_id == twin.task_id
        assert original.gates["G6"].passed and original.gates["G6"].evidence["status"] == "changed"
        assert twin.answer != original.answer
        assert twin.gates["G6"].evidence["status"] == "is_twin"
        twin_scene = read_scene(lay.twin_layout().scene_file(twin.scene_id))
        assert twin_scene.meta.twin_of == original.scene_id and not twin_scene.meta.settled
        assert [c.id for c in twin_scene.cameras] == [original.camera_id]
        assert all(twin.gates[g].passed for g in ("G1", "G2", "G3", "G4", "G7"))
    statuses = {t.gates["G6"].evidence["status"] for t in tasks.values() if not t.twin_of}
    assert statuses <= {"changed", "not_sampled", "no_twin_rule", "twin_invalid"}
    assert report["gates"]["G6"]["sampled"] >= len(twins)

    # a third start finds nothing to do
    third = FakeRenderer()
    monkeypatch.setattr(bridge, "render_scene", third)
    again = scale_run(**args)
    assert third.calls == []
    assert again["accepted"] == report["accepted"]
    assert sorted(r["task_id"] for r in read_jsonl(lay.task_manifest)) == sorted(tasks)


def test_stratified_sample() -> None:
    from vtf.validate.gates.g6_counterfactual_twin import stratified_sample

    rows = [{"task_id": f"t{i:03d}", "family": f, "rig": r}
            for i, (f, r) in enumerate([("count", "front")] * 10 + [("relation", "overhead")] * 3
                                       + [("support", "front")])]  # fmt: skip
    picked = stratified_sample(rows, 0.2)
    by = {(f, r): sum(1 for x in rows if x["task_id"] in picked and (x["family"], x["rig"]) ==
                      (f, r)) for f, r in {("count", "front"), ("relation", "overhead"),
                                           ("support", "front")}}  # fmt: skip
    assert by == {("count", "front"): 2, ("relation", "overhead"): 1, ("support", "front"): 1}
    assert stratified_sample(rows, 0.2) == picked  # deterministic
    assert stratified_sample(rows, 0.0) == set()
