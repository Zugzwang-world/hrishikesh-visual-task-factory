"""G2 (pixel route per family), G3 and G4 on synthetic passes from the analytic ray caster."""

import math

import numpy as np
import pytest

from vtf.core.assets import ContainerInfo
from vtf.core.geometry import Pose, look_at_camera_quat, quat_from_axis_angle
from vtf.core.hashing import sha256_json
from vtf.core.program import CompiledQuestion, ProgramRef, ProgramResult
from vtf.core.projection import intrinsics, world_to_camera
from vtf.core.raycast import raycast
from vtf.core.render_manifest import CameraOutputs
from vtf.core.scene import (
    CameraSpec,
    Lighting,
    SceneMeta,
    SceneObject,
    SceneSpec,
    Table,
    Zone,
    build_gripper,
)
from vtf.validate.gates import g2_second_computation as g2
from vtf.validate.gates import g3_evidence_visible as g3
from vtf.validate.gates.g4_no_near_ties import check_g4
from vtf.validate.pixels import CameraPasses

TOP = 0.75
RES = (480, 360)


def obj(i, shape, size, x, y, colour="red", category=None, z=None, yaw=0.0, size_word=None):
    dims = size if isinstance(size, tuple) else (size, size, size)
    zc = TOP + dims[2] / 2 if z is None else z
    return SceneObject(
        id=f"obj_{i:02d}", shape=shape, category=category or shape, color_name=colour,
        size_word=size_word, rgb=(0.5, 0.2, 0.2), dimensions=dims,
        pose=Pose(position=(x, y, zc),
                  rotation=quat_from_axis_angle((0, 0, 1), math.radians(yaw))),
        pass_index=i + 1,
    )  # fmt: skip


def camera(cid, rig, eye, target, up=(0.0, 0.0, 1.0), res=RES, focal=28.0):
    return CameraSpec(
        id=cid, rig=rig, focal_mm=focal, resolution=res,
        pose=Pose(position=eye, rotation=look_at_camera_quat(eye, target, up)),
    )  # fmt: skip


FRONT = camera("front", "front", (0.0, -0.9 * math.cos(0.7), TOP + 0.9 * math.sin(0.7)),
               (0.0, 0.0, TOP))  # fmt: skip
OVERHEAD = camera("overhead", "overhead", (0.0, 0.0, TOP + 1.0), (0.0, 0.0, TOP), up=(0, 1, 0))


def scene(objects, cameras=(FRONT, OVERHEAD), tip=(0.3, 0.25, TOP + 0.35), zones=()):
    return SceneSpec(
        meta=SceneMeta(scene_id="scene_9000", generator_version="test", seed=0,
                       recipe_hash="0" * 64, settled=True),
        table=Table(size_xy=(1.0, 0.7), height=TOP),
        objects=list(objects),
        gripper=build_gripper(Pose(position=tip)),
        zones=list(zones),
        cameras=list(cameras),
        lighting=Lighting(hdri_id="studio_small_09"),
    )  # fmt: skip


_RAYCASTS: dict = {}


def passes(s: SceneSpec, cam_id: str = "front") -> CameraPasses:
    """Exact passes from the ray caster (cached: the ray cast is the slow part)."""
    cam = s.camera(cam_id)
    key = (sha256_json(s), cam_id)
    if key not in _RAYCASTS:
        _RAYCASTS[key] = raycast(s, cam_id)
    rc = _RAYCASTS[key]
    out = CameraOutputs(
        camera_id=cam.id, rig=cam.rig, resolution=cam.resolution, beauty="b.png",
        index="i.png", depth="d.npz", amodal={},
        K=tuple(tuple(float(v) for v in r) for r in intrinsics(cam)),
        world_to_camera=tuple(tuple(float(v) for v in r) for r in world_to_camera(cam)),
        render_seconds=0.0, amodal_seconds=0.0,
    )  # fmt: skip
    return CameraPasses(cam=cam, out=out, index=rc.index.copy(), depth=rc.depth, amodal=rc.amodal)


def q(program, params, answer, answer_type, family=None, evidence=(), cam="front", **kw):
    return CompiledQuestion(
        family=family or program, template_id=f"{program}.p1", question="?", camera_id=cam,
        program=ProgramRef(name=program, params=params),
        result=ProgramResult(answer=answer, answer_type=answer_type, answer_text=str(answer),
                             evidence_object_ids=list(evidence), **kw),
    )  # fmt: skip


def g2_value(s, question, cam="front"):
    value, _ = g2.recompute(question, s, passes(s, cam))
    return value


# ---------------------------------------------------------------------------------------------
# G2 per family


BASIC = [
    obj(0, "box", 0.06, -0.20, 0.05, "red"),
    obj(1, "cylinder", (0.05, 0.05, 0.08), 0.05, 0.12, "blue"),
    obj(2, "sphere", 0.06, 0.20, -0.05, "red"),
    obj(3, "cylinder", (0.06, 0.06, 0.05), -0.05, -0.12, "green"),
]


@pytest.mark.parametrize("cam", ["front", "overhead"])
def test_g2_counts(cam) -> None:
    s = scene(BASIC)
    assert g2_value(s, q("count_all", {}, 4, "int"), cam) == 4
    assert g2_value(s, q("count_category", {"category": "cylinder"}, 2, "int"), cam) == 2
    assert g2_value(s, q("count_color", {"color": "red"}, 2, "int"), cam) == 2
    good = g2.check_g2(q("count_all", {}, 4, "int", family="count"), s, passes(s, cam))
    bad = g2.check_g2(q("count_all", {}, 5, "int", family="count"), s, passes(s, cam))
    assert good.passed and not bad.passed
    assert bad.evidence["reason"] == "pixel_answer_differs"


def _tray(monkeypatch, tray_id="obj_00"):
    info = ContainerInfo(shape="rect", inner_size_xy=(0.2, 0.2), floor_z_m=0.01, rim_z_m=0.03)
    monkeypatch.setattr(g2, "container_info", lambda o: info if o.id == tray_id else None)


TRAY_SCENE = [
    obj(0, "box", (0.22, 0.22, 0.01), 0.0, 0.05, "white", category="tray"),
    obj(1, "box", 0.05, -0.05, 0.08, "red", z=TOP + 0.01 + 0.025),
    obj(2, "sphere", 0.05, 0.05, 0.02, "blue", z=TOP + 0.01 + 0.025),
    obj(3, "box", 0.05, 0.25, -0.10, "red"),
]


@pytest.mark.parametrize("cam", ["front", "overhead"])
def test_g2_container_families(monkeypatch, cam) -> None:
    _tray(monkeypatch)
    s = scene(TRAY_SCENE)
    params = {"color": "red", "container": "obj_00"}
    assert g2_value(s, q("count_color_in", params, 1, "int"), cam) == 1
    assert g2_value(s, q("contents", {"container": "obj_00"}, [], "set"), cam) == [
        "obj_01",
        "obj_02",
    ]
    res = g2.check_g2(
        q("contents", {"container": "obj_00"}, ["blue sphere", "red box"], "set",
          family="containment"), s, passes(s, cam))  # fmt: skip
    assert not res.passed  # "red box" names two objects: unresolvable truth
    res = g2.check_g2(
        q("contents", {"container": "obj_00"}, ["obj_01", "sphere"], "set", family="containment"),
        s, passes(s, cam),
    )  # fmt: skip
    assert res.passed, res.evidence


def test_g2_nearest_to_gripper() -> None:
    s = scene(BASIC, tip=(0.20, -0.05, TOP + 0.12))  # above the red sphere
    assert g2_value(s, q("nearest_to_gripper", {}, "", "object")) == "obj_02"
    res = g2.check_g2(q("nearest_to_gripper", {}, "sphere", "object"), s, passes(s))
    assert res.passed, res.evidence


def test_g2_distance() -> None:
    s = scene([obj(0, "box", 0.06, -0.10, 0.0, "red"), obj(1, "box", 0.06, 0.15, 0.0, "blue")])
    for cam in ("front", "overhead"):
        d = g2_value(s, q("distance_cm", {"a": "obj_00", "b": "obj_01"}, 25.0, "number"), cam)
        assert abs(d - 25.0) <= 2.0
    ok = g2.check_g2(q("distance_cm", {"a": "obj_00", "b": "obj_01"}, 25.0, "number",
                       family="distance", tolerance=2.5), s, passes(s))  # fmt: skip
    far = g2.check_g2(q("distance_cm", {"a": "obj_00", "b": "obj_01"}, 35.0, "number",
                        family="distance", tolerance=3.5), s, passes(s))  # fmt: skip
    assert ok.passed and not far.passed


def test_g2_front_to_back() -> None:
    s = scene(BASIC)
    params = {"a": "obj_01", "b": "obj_03"}  # blue cylinder at y=0.12, green at y=-0.12
    assert g2_value(s, q("front_to_back", params, "", "object")) == "obj_03"
    assert g2.check_g2(q("front_to_back", params, "green cylinder", "object"), s, passes(s)).passed
    assert not g2.check_g2(q("front_to_back", params, "blue cylinder", "object"), s,
                           passes(s)).passed  # fmt: skip


@pytest.mark.parametrize("cam", ["front", "overhead"])
def test_g2_relation(cam) -> None:
    s = scene([
        obj(0, "box", 0.06, 0.0, 0.0, "red"),
        obj(1, "box", 0.05, 0.15, 0.02, "blue"),
        obj(2, "box", 0.05, 0.30, 0.0, "green"),
        obj(3, "sphere", 0.05, -0.12, -0.02, "yellow"),
    ])  # fmt: skip
    assert g2_value(s, q("relation", {"anchor": "obj_00", "direction": "right"}, "", "object"),
                    cam) == "obj_01"  # fmt: skip
    assert g2_value(s, q("relation", {"anchor": "obj_00", "direction": "left"}, "", "object"),
                    cam) == "obj_03"  # fmt: skip


@pytest.mark.parametrize("cam", ["front", "overhead"])
def test_g2_support(cam) -> None:
    big = obj(0, "box", (0.12, 0.12, 0.06), 0.05, 0.05, "white")
    small = obj(1, "box", 0.04, 0.05, 0.05, "red", z=TOP + 0.06 + 0.02)
    ball = obj(2, "sphere", 0.05, -0.2, -0.05, "blue")
    s = scene([big, small, ball])
    assert g2_value(s, q("support", {"a": "obj_01"}, "", "object"), cam) == "obj_00"
    assert g2_value(s, q("support", {"a": "obj_02"}, "", "object"), cam) == "table"
    assert g2.check_g2(q("support", {"a": "obj_02"}, "table", "object"), s, passes(s, cam)).passed


def test_g2_occlusion() -> None:
    s = scene([
        obj(0, "box", (0.12, 0.03, 0.10), 0.0, -0.06, "red"),  # wall in front
        obj(1, "cylinder", (0.05, 0.05, 0.10), 0.03, 0.0, "blue"),  # partly behind it
        obj(2, "sphere", 0.05, 0.3, 0.1, "green"),
    ])  # fmt: skip
    assert g2_value(s, q("occluded_by", {"occluder": "obj_00"}, "", "object")) == "obj_01"
    ok = g2.check_g2(q("occluded_by", {"occluder": "obj_00"}, "cylinder", "object",
                       family="occlusion"), s, passes(s))  # fmt: skip
    assert ok.passed, ok.evidence


@pytest.mark.parametrize("cam", ["front", "overhead"])
def test_g2_reachability(cam) -> None:
    zone = Zone(id="zone_a", shape="circle", center_xy=(0.2, 0.0), radius=0.1)
    rect = Zone(id="zone_b", shape="rect", center_xy=(-0.2, 0.0), size_xy=(0.2, 0.1), yaw_deg=30)
    s = scene(
        [obj(0, "box", 0.05, 0.2, 0.03, "red"), obj(1, "box", 0.05, -0.2, 0.01, "blue"),
         obj(2, "sphere", 0.05, 0.0, 0.2, "green")],
        zones=[zone, rect],
    )  # fmt: skip
    assert g2_value(s, q("in_zone", {"zone": "zone_a"}, [], "set"), cam) == ["obj_00"]
    assert g2_value(s, q("in_zone", {"zone": "zone_b"}, [], "set"), cam) == ["obj_01"]


def test_g2_free_space() -> None:
    a, b = obj(0, "box", 0.06, -0.15, 0.0, "red"), obj(1, "box", 0.06, 0.15, 0.0, "blue")
    item = obj(2, "cylinder", (0.06, 0.06, 0.08), 0.3, 0.25, "green")
    params = {"item": "obj_02", "a": "obj_00", "b": "obj_01"}
    assert g2_value(scene([a, b, item]), q("room_between", params, True, "bool")) is True
    blocker = obj(3, "box", 0.06, 0.0, 0.0, "yellow")
    assert g2_value(scene([a, b, item, blocker]), q("room_between", params, False, "bool")) is False
    near = obj(1, "box", 0.06, -0.05, 0.0, "blue")  # 4 cm gap < item width
    assert g2_value(scene([a, near, item]), q("room_between", params, False, "bool")) is False


def test_g2_gripper_center() -> None:
    target = obj(0, "box", 0.08, 0.1, 0.05, "red")
    tip = (0.1, 0.05, TOP + 0.25)
    grip = camera("gripper", "gripper", tip, (0.1, 0.05, TOP), up=(0, 1, 0))
    s = scene([target, obj(1, "sphere", 0.05, -0.2, 0.0, "blue")], cameras=[grip], tip=tip)
    assert g2_value(s, q("gripper_center", {}, "", "object", cam="gripper"), "gripper") == "obj_00"
    res = check_g4(q("gripper_center", {}, "box", "object", family="gripper_view", cam="gripper"),
                   s, "gripper", passes(s, "gripper"))  # fmt: skip
    assert res.passed, res.evidence


def test_render_vs_scene_iou() -> None:
    s = scene(BASIC)
    check = g2.render_vs_scene(s, passes(s))
    assert check["camera_ok"] and check["min_raycast_iou"] == 1.0
    p = passes(s)
    p.index = p.index.copy()
    p.index[p.index == 2] = 1000  # the blue cylinder vanishes from the "render"
    check = g2.render_vs_scene(s, p)
    res = g2.check_g2(q("count_all", {}, 3, "int", evidence=["obj_01"]), s, p, check)
    assert not res.passed
    assert res.evidence["render_vs_scene"]["failing"] == ["obj_01"]


# ---------------------------------------------------------------------------------------------
# G3


def test_g3_count_thresholds_and_scale() -> None:
    table = {1: g3.Visibility(150, 1000), 2: g3.Visibility(400, 500)}
    pio = {"obj_00": 1, "obj_01": 2}
    count = g3.check_g3(["obj_00", "obj_01"], pio, table, family="count", params={})
    other = g3.check_g3(["obj_00", "obj_01"], pio, table, family="front_to_back", params={})
    assert count.passed and not other.passed
    s = scene(BASIC)
    assert g3.check_g3(["obj_01"], pio, table, family="distance", scene=s).evidence["reason"] == (
        "no_scale_in_frame"
    )
    gridded = s.model_copy(update={"table": s.table.model_copy(update={"grid_cell_m": 0.05})})
    assert g3.check_g3(["obj_01"], pio, table, family="distance", scene=gridded).passed


# ---------------------------------------------------------------------------------------------
# G4


@pytest.mark.parametrize(
    "family, rig, margin, margin_px, ok",
    [
        ("nearest_to_gripper", "overhead", 0.04, 12, True),
        ("nearest_to_gripper", "front", 0.04, 12, False),  # front rig needs 5 cm
        ("nearest_to_gripper", "front", 0.05, 12, True),
        ("nearest_to_gripper", "overhead", 0.04, 9, False),  # pixel margin 10 px
        ("front_to_back", "gripper", 0.041, 10, True),
        ("front_to_back", "front", 0.059, 30, False),
        ("relation", "front", 0.031, 11, True),
        ("relation", "front", 0.029, 11, False),
        ("reachability", "overhead", 0.021, 6, True),
        ("reachability", "overhead", 0.021, 5.9, False),
        ("free_space", "front", 0.015, 7, True),
        ("free_space", "front", 0.014, 7, False),
        ("reachability", "front", None, 7, False),  # a missing margin never passes
        ("containment", "front", 0.012, None, True),
        ("containment", "front", 0.008, None, False),
        ("count", "front", None, None, True),
    ],
)
def test_g4_thresholds(family, rig, margin, margin_px, ok) -> None:
    s = scene(BASIC)
    question = q("x", {}, "red box", "object", family=family, evidence=["obj_00", "obj_01"],
                 margin=margin, margin_px=margin_px)  # fmt: skip
    res = check_g4(question, s, rig)
    assert res.passed is ok, res.evidence


def test_g4_distance_and_pixel_fallback() -> None:
    s = scene(BASIC)
    assert not check_g4(q("distance_cm", {}, 7.9, "number", family="distance"), s, "front").passed
    assert check_g4(q("distance_cm", {}, 8.0, "number", family="distance"), s, "front").passed
    fb = check_g4(q("front_to_back", {}, "x", "object", family="front_to_back",
                    evidence=["obj_01", "obj_03"], margin=0.24), s, "front")  # fmt: skip
    assert fb.passed and fb.evidence["pixel_margin_source"] == "fallback:image_points"


def test_g4_occlusion_runner_up() -> None:
    wall = obj(0, "box", (0.20, 0.03, 0.10), 0.0, -0.06, "red")
    behind = obj(1, "cylinder", (0.05, 0.05, 0.10), 0.03, 0.0, "blue")
    twin = obj(2, "cylinder", (0.05, 0.05, 0.10), -0.05, 0.0, "green")
    s = scene([wall, behind, twin])
    res = check_g4(q("occluded_by", {"occluder": "obj_00"}, "blue cylinder", "object",
                     family="occlusion"), s, "front", passes(s))  # fmt: skip
    assert not res.passed and res.evidence["reason"] == "occlusion_runner_up_close"
    lone = scene([wall, behind, obj(2, "sphere", 0.05, 0.3, 0.1, "green")])
    res = check_g4(q("occluded_by", {"occluder": "obj_00"}, "blue cylinder", "object",
                     family="occlusion"), lone, "front", passes(lone))  # fmt: skip
    assert res.passed, res.evidence


def test_back_projection_recovers_surface_points() -> None:
    s = scene(BASIC)
    p = passes(s)
    pts = p.points(BASIC[0].pass_index)
    box = BASIC[0]
    local = (pts - np.asarray(box.pose.position)) @ box.pose.rotation_matrix()
    assert np.abs(local).max() <= 0.03 + 1e-6  # every point lies on the 6 cm box
