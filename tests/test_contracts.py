"""Contract tests: schemas are current, fixtures validate, conventions hold."""

from pathlib import Path

import numpy as np
import pytest

from vtf.core import layout
from vtf.core.geometry import look_at_camera_quat, matrix_to_quat, quat_to_matrix
from vtf.core.hashing import sha256_json
from vtf.core.io import (
    read_depth,
    read_index_png,
    read_jsonl,
    read_manifest,
    read_mask_png,
    read_scene,
    read_task,
)
from vtf.core.projection import intrinsics, project, world_to_camera
from vtf.core.raycast import raycast
from vtf.core.rng import derive_seed
from vtf.core.schemas import SCHEMAS, schema_text
from vtf.core.task import GateResult
from vtf.core.vocab import normalise_bool, normalise_int, normalise_object, normalise_set

REPO = Path(__file__).resolve().parents[1]
SPEC = REPO / "spec"
RUN = SPEC / "examples" / "run_example"


def test_schemas_are_current() -> None:
    for name, (model, version) in SCHEMAS.items():
        assert (SPEC / name).read_text() == schema_text(model, version), (
            f"spec/{name} is stale: run `uv run python -m vtf.core.schemas`"
        )


def test_example_scene_validates() -> None:
    scene = read_scene(SPEC / "examples" / "scene_example.json")
    assert not scene.meta.settled
    assert len({o.name for o in scene.objects}) == len(scene.objects)


def test_fixture_run_is_consistent() -> None:
    lay = layout.RunLayout(RUN)
    assert lay.scene_ids() == ["scene_0000"]
    sid = "scene_0000"
    manifest = read_manifest(lay.render_manifest(sid))
    settled = read_scene(lay.settled_scene(sid))
    assert settled.meta.settled
    cam_out = manifest.cameras[0]
    cam = settled.camera(cam_out.camera_id)
    np.testing.assert_allclose(cam_out.K, intrinsics(cam), atol=1e-5)
    np.testing.assert_allclose(cam_out.world_to_camera, world_to_camera(cam), atol=1e-5)
    rdir = lay.render_dir(sid)
    index = read_index_png(rdir / cam_out.index)
    depth = read_depth(rdir / cam_out.depth)
    assert index.shape == depth.shape == (cam.resolution[1], cam.resolution[0])
    for key, rel in cam_out.amodal.items():
        amodal = read_mask_png(rdir / rel)
        visible = index == int(key)
        assert not (visible & ~amodal).any(), "visible pixels must lie inside the amodal mask"
    # The ray caster reproduces the fixture exactly.
    rc = raycast(settled, cam.id)
    assert np.array_equal(rc.index, index)
    for row in read_jsonl(lay.task_manifest):
        task = read_task(RUN / row["path"])
        assert task.task_id == row["task_id"]
        assert task.scene_hash == sha256_json(settled)
        assert (lay.task_dir(task.task_id) / task.image).exists()


def test_object_centres_project_where_the_raycaster_sees_them() -> None:
    settled = read_scene(RUN / "scenes" / "scene_0000" / "render" / layout.SETTLED_SCENE)
    cam = settled.cameras[0]
    rc = raycast(settled, cam.id)
    for obj in settled.objects:
        uv, z = project(cam, np.array([obj.pose.position]))
        u, v = uv[0]
        if rc.index[int(v), int(u)] == obj.pass_index:
            assert 0 < z[0] <= rc.depth[int(v), int(u)] + max(obj.dimensions)


def test_quaternions_round_trip() -> None:
    q = look_at_camera_quat((0.3, -0.7, 1.3), (0.0, 0.0, 0.75))
    np.testing.assert_allclose(matrix_to_quat(quat_to_matrix(q)), q, atol=1e-12)
    rot = quat_to_matrix(q)
    np.testing.assert_allclose(rot @ rot.T, np.eye(3), atol=1e-12)
    # The camera looks down its local -Z at the target.
    forward = -rot[:, 2]
    target_dir = np.array([0.0, 0.0, 0.75]) - np.array([0.3, -0.7, 1.3])
    np.testing.assert_allclose(forward, target_dir / np.linalg.norm(target_dir), atol=1e-12)


def test_gate_result_serialises_pass() -> None:
    g = GateResult(passed=True, evidence={"x": 1})
    assert g.model_dump(mode="json") == {"pass": True, "evidence": {"x": 1}}
    assert GateResult.model_validate({"pass": False}).passed is False


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("red box", "red box"),
        ("The red cube.", "red box"),
        ("a small BLUE ball", "blue sphere"),
        ("violet cone", "purple cone"),
        ("red", None),
        ("red box or blue box", None),
        ("red and blue cube", None),
    ],
)
def test_normalise_object(text: str, expected: str | None) -> None:
    assert normalise_object(text) == expected


def test_normalise_other_types() -> None:
    assert normalise_int("seven") == 7
    assert normalise_int("7 objects") == 7
    assert normalise_int(3) == 3
    assert normalise_int(True) is None
    assert normalise_int("3 or 4") is None
    assert normalise_bool("Yes.") is True
    assert normalise_bool("no") is False
    assert normalise_set("the red cube and a blue ball") == ["blue sphere", "red box"]


def test_derive_seed_is_stable() -> None:
    assert derive_seed(1, 0) == derive_seed(1, 0)
    assert derive_seed(1, 0) != derive_seed(1, 1)
    assert 0 <= derive_seed("x") < 2**63
