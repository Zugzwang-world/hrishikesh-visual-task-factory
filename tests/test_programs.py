"""Question programs v0.2 on primitive scenes: the exact shape geometry the programs rest on,
brute-force checks of count, distance, relation and reach zones, the G4 near-tie thresholds, the
naming rule, and run_program's contract (spec/families.md).

Scenes are built in-test, so these tests do not depend on the sampler lane. Asset scenes
(containers, stacks, twins, compile_questions) are in tests/test_programs_v2.py.
"""

import math
from pathlib import Path

import numpy as np
import pytest
from hypothesis import HealthCheck, event, given, settings
from hypothesis import strategies as st

from vtf.core.geometry import Pose, look_at_camera_quat, quat_from_axis_angle, quat_multiply
from vtf.core.io import read_scene, read_task
from vtf.core.program import FAMILIES, PROGRAM_FAMILY, ProgramReject
from vtf.core.projection import to_camera
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
from vtf.core.vocab import COLOURS, SHAPES, linear_rgb, resolve_object
from vtf.programs import (
    NEAR_TIE_DEPTH_FRONT_M,
    NEAR_TIE_DEPTH_M,
    NEAR_TIE_GRIPPER_FRONT_M,
    NEAR_TIE_GRIPPER_M,
    PROGRAMS,
    PROGRAMS_VERSION,
    run_program,
)
from vtf.programs.shapes import lowest_z, point_distance
from vtf.programs.view import camera_right_xy

REPO = Path(__file__).resolve().parents[1]
RUN = REPO / "spec" / "examples" / "run_example"
FIXTURE = RUN / "scenes" / "scene_0000" / "render" / "scene.settled.json"
TABLE_H = 0.75
CAM = "front"
SETTINGS = settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
FEW = settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])


# ---------------------------------------------------------------------------------------------
# scene builders


def make_object(i, shape, colour, dims, position, rotation=(1.0, 0.0, 0.0, 0.0)) -> SceneObject:
    return SceneObject(
        id=f"obj_{i:02d}",
        shape=shape,
        category=shape,
        color_name=colour,
        rgb=linear_rgb(colour),
        dimensions=tuple(dims),
        pose=Pose(position=tuple(position), rotation=tuple(rotation)),
        pass_index=i + 1,
    )


def make_scene(
    objects,
    tip=(0.0, 0.0, 0.95),
    eye=(0.0, -0.69, 1.33),
    target=(0.0, 0.0, TABLE_H),
    settled=True,
    rig="front",
    zones=(),
    grid=None,
) -> SceneSpec:
    return SceneSpec(
        meta=SceneMeta(
            scene_id="scene_test",
            generator_version="test",
            seed=0,
            recipe_hash="0" * 64,
            settled=settled,
        ),
        table=Table(size_xy=(1.0, 0.7), height=TABLE_H, grid_cell_m=grid),
        objects=list(objects),
        gripper=build_gripper(Pose(position=tuple(tip))),
        zones=list(zones),
        cameras=[
            CameraSpec(
                id=CAM,
                rig=rig,
                pose=Pose(position=tuple(eye), rotation=look_at_camera_quat(eye, target)),
                focal_mm=28.0,
                resolution=(320, 240),
            )
        ],
        lighting=Lighting(hdri_id="studio_small_09"),
    )


def sphere(i, colour, radius, centre) -> SceneObject:
    d = 2 * radius
    return make_object(i, "sphere", colour, (d, d, d), centre)


def outcome(name: str, params: dict, scene: SceneSpec) -> dict:
    try:
        return run_program(name, params, scene, CAM).model_dump()
    except ProgramReject as rej:
        return {"rejected": rej.reason, "detail": rej.detail}


# ---------------------------------------------------------------------------------------------
# strategies

sizes = st.floats(0.04, 0.12)
unit = st.floats(-1.0, 1.0)
angles = st.floats(0.0, 2 * math.pi)
quats = st.tuples(angles, angles, angles).map(
    lambda zyx: quat_multiply(
        quat_from_axis_angle((0.0, 0.0, 1.0), zyx[0]),
        quat_multiply(
            quat_from_axis_angle((0.0, 1.0, 0.0), zyx[1]),
            quat_from_axis_angle((1.0, 0.0, 0.0), zyx[2]),
        ),
    )
)
centres = st.tuples(st.floats(-0.5, 0.5), st.floats(-0.5, 0.5), st.floats(0.5, 1.2))


@st.composite
def dims_for(draw, shape: str) -> tuple[float, float, float]:
    if shape == "box":
        return (draw(sizes), draw(sizes), draw(sizes))
    if shape == "sphere":
        d = draw(sizes)
        return (d, d, d)
    d, h = draw(sizes), draw(sizes)
    return (d, d, h)


@st.composite
def scenes(draw, placements=("on",), min_objects=1, max_objects=10):
    """Upright objects with a random yaw: on the table (lowest point within 9 mm of the top),
    on the floor beside it, or lifted above it (stacked or floating). Returns (scene, status)."""
    pairs = draw(
        st.lists(
            st.tuples(st.sampled_from(COLOURS), st.sampled_from(SHAPES)),
            min_size=min_objects,
            max_size=max_objects,
            unique=True,
        )
    )
    objects, status = [], {}
    for i, (colour, shape) in enumerate(pairs):
        dims = draw(dims_for(shape))
        where = draw(st.sampled_from(placements))
        yaw = draw(st.floats(0.0, 2 * math.pi))
        half = dims[2] / 2
        if where == "floor":
            x = draw(st.floats(0.6, 0.9)) * draw(st.sampled_from((-1, 1)))
            y, z = draw(st.floats(-0.3, 0.3)), half
        else:
            x, y = draw(st.floats(-0.45, 0.45)), draw(st.floats(-0.3, 0.3))
            lift = draw(st.floats(-0.009, 0.009) if where == "on" else st.floats(0.02, 0.1))
            z = TABLE_H + half + lift
        rotation = quat_from_axis_angle((0.0, 0.0, 1.0), yaw)
        objects.append(make_object(i, shape, colour, dims, (x, y, z), rotation))
        status[objects[-1].id] = where
    tip = (draw(st.floats(-0.4, 0.4)), draw(st.floats(-0.3, 0.3)), draw(st.floats(0.87, 1.0)))
    return make_scene(objects, tip=tip), status


# ---------------------------------------------------------------------------------------------
# geometry: point-to-surface distance and lowest point


def surface_samples(shape: str, dims, n: int = 96) -> np.ndarray:
    """Dense points on the surface of a solid, in its local frame."""
    dx, dy, dz = dims
    if shape == "box":
        g = np.linspace(-0.5, 0.5, n)
        a, b = (m.ravel() for m in np.meshgrid(g, g))
        pts = []
        for s in (-0.5, 0.5):
            f = np.full_like(a, s)
            pts += [np.c_[f * dx, a * dy, b * dz], np.c_[a * dx, f * dy, b * dz]]
            pts += [np.c_[a * dx, b * dy, f * dz]]
        return np.vstack(pts)
    r, h = dx / 2, dz / 2
    t, th = (m.ravel() for m in np.meshgrid(np.linspace(0, 1, n), np.linspace(0, 2 * np.pi, 4 * n)))
    c, s = np.cos(th), np.sin(th)
    disc = [np.c_[r * t * c, r * t * s, np.full_like(t, z)] for z in (-h, h)]
    if shape == "cylinder":
        return np.vstack([np.c_[r * c, r * s, -h + 2 * h * t], *disc])
    if shape == "cone":
        rad = r * (1 - t)
        return np.vstack([np.c_[rad * c, rad * s, -h + 2 * h * t], disc[0]])
    raise ValueError(shape)


def inside_local(shape: str, dims, q) -> bool:
    """Definitional inside test, written independently of the programs."""
    dx, dy, dz = dims
    r, h = dx / 2, dz / 2
    rho = math.hypot(q[0], q[1])
    if shape == "box":
        return abs(q[0]) <= dx / 2 and abs(q[1]) <= dy / 2 and abs(q[2]) <= dz / 2
    if shape == "cylinder":
        return rho <= r and abs(q[2]) <= h
    return abs(q[2]) <= h and rho <= r * (h - q[2]) / (2 * h)


def world(obj: SceneObject, local: np.ndarray) -> np.ndarray:
    return np.asarray(local).reshape(-1, 3) @ obj.pose.rotation_matrix().T + obj.pose.position


@SETTINGS
@given(
    shape=st.sampled_from(SHAPES),
    data=st.data(),
    rotation=quats,
    centre=centres,
    frac=st.tuples(unit, unit, unit),
)
def test_distance_is_zero_inside(shape, data, rotation, centre, frac) -> None:
    dims = data.draw(dims_for(shape))
    obj = make_object(0, shape, "red", dims, centre, rotation)
    u, v, w = (0.98 * f for f in frac)
    r, h = dims[0] / 2, dims[2] / 2
    if shape == "box":
        q = (u * dims[0] / 2, v * dims[1] / 2, w * dims[2] / 2)
    elif shape == "sphere":
        q = (u * r / math.sqrt(3), v * r / math.sqrt(3), w * r / math.sqrt(3))
    elif shape == "cylinder":
        q = (u * r / math.sqrt(2), v * r / math.sqrt(2), w * h)
    else:
        allowed = r * (h - w * h) / (2 * h)
        q = (u * allowed / math.sqrt(2), v * allowed / math.sqrt(2), w * h)
    assert point_distance(obj, world(obj, q)[0]) == 0.0


SLANT_NORMAL = (0.08 / math.hypot(0.03, 0.08), 0.03 / math.hypot(0.03, 0.08))  # (rho, z)


@pytest.mark.parametrize(
    "shape, local, expected",
    [
        ("box", (0.05, 0.05, 0.05), 0.02 * math.sqrt(3)),  # off a corner
        ("box", (0.0, 0.0, 0.031), 0.001),
        ("cylinder", (0.05, 0.0, 0.0), 0.02),  # beside the side wall
        ("cylinder", (0.05, 0.0, 0.07), math.hypot(0.02, 0.03)),  # off the top rim
        ("cylinder", (0.02, 0.0, -0.039), 0.0),
        ("cone", (0.015, 0.0, -0.02), 0.0),  # inside, near the base
        ("cone", (0.0, 0.0, 0.05), 0.01),  # above the apex
        ("cone", (0.0, 0.0, -0.05), 0.01),  # under the base
        ("cone", (0.05, 0.0, -0.04), 0.02),  # beside the base rim
        ("cone", (0.015 + 0.01 * SLANT_NORMAL[0], 0.0, 0.01 * SLANT_NORMAL[1]), 0.01),  # off slant
    ],
)
def test_distance_hand_computed(shape, local, expected) -> None:
    """Cube 6 cm; cylinder 6 cm wide, 8 cm tall; cone 6 cm base, 8 cm tall. Points are given in
    the object's frame and placed in the world through an arbitrary pose."""
    dims = (0.06, 0.06, 0.06) if shape == "box" else (0.06, 0.06, 0.08)
    tilt = quat_multiply(
        quat_from_axis_angle((0.0, 0.0, 1.0), 0.7), quat_from_axis_angle((1.0, 0.0, 0.0), 0.3)
    )
    obj = make_object(0, shape, "red", dims, (0.1, -0.2, 0.8), tilt)
    assert point_distance(obj, world(obj, local)[0]) == pytest.approx(expected, abs=1e-12)


@SETTINGS
@given(radius=st.floats(0.02, 0.06), rotation=quats, centre=centres, point=centres)
def test_sphere_distance_is_analytic(radius, rotation, centre, point) -> None:
    obj = make_object(0, "sphere", "red", (2 * radius,) * 3, centre, rotation)
    expected = max(math.dist(point, centre) - radius, 0.0)
    assert point_distance(obj, point) == pytest.approx(expected, abs=1e-12)


@SETTINGS
@given(
    shape=st.sampled_from(("box", "cylinder", "cone")),
    data=st.data(),
    rotation=quats,
    centre=centres,
    offset=st.tuples(*[st.floats(-0.15, 0.15)] * 3),
)
def test_distance_matches_brute_force(shape, data, rotation, centre, offset) -> None:
    dims = data.draw(dims_for(shape))
    obj = make_object(0, shape, "red", dims, centre, rotation)
    point = np.asarray(centre) + np.asarray(offset)
    analytic = point_distance(obj, point)
    to_surface = float(
        np.linalg.norm(world(obj, surface_samples(shape, dims)) - point, axis=1).min()
    )
    q_local = obj.pose.rotation_matrix().T @ (point - np.asarray(centre))
    assert analytic <= to_surface + 1e-9  # the surface is part of the solid
    event(f"{shape} {'inside' if inside_local(shape, dims, q_local) else 'outside'}")
    if inside_local(shape, dims, q_local):
        assert analytic == 0.0
    else:
        assert to_surface - analytic <= 1.5e-3  # sampling resolution


COS30, SIN30 = math.cos(math.pi / 6), 0.5


@pytest.mark.parametrize(
    "shape, tilt_deg, drop",
    [
        ("cylinder", 0, 0.04),  # upright: half the height
        ("cylinder", 90, 0.03),  # on its side: the radius
        ("cylinder", 30, 0.04 * COS30 + 0.03 * SIN30),  # on its rim
        ("cone", 0, 0.04),  # on its base
        ("cone", 180, 0.04),  # on its apex
        ("cone", 90, 0.03),  # axis level: the base rim is lowest
        ("cone", 30, 0.04 * COS30 + 0.03 * SIN30),  # on its base rim
        ("cone", 150, 0.04 * COS30),  # apex down
    ],
)
def test_lowest_point_hand_computed(shape, tilt_deg, drop) -> None:
    """6 cm wide, 8 cm tall, tilted about x then turned about z: centre z minus the drop."""
    tilt = quat_multiply(
        quat_from_axis_angle((0.0, 0.0, 1.0), 1.1),
        quat_from_axis_angle((1.0, 0.0, 0.0), math.radians(tilt_deg)),
    )
    obj = make_object(0, shape, "red", (0.06, 0.06, 0.08), (0.0, 0.0, 1.0), tilt)
    assert lowest_z(obj) == pytest.approx(1.0 - drop, abs=1e-12)


@SETTINGS
@given(shape=st.sampled_from(SHAPES), data=st.data(), rotation=quats, centre=centres)
def test_lowest_point_matches_brute_force(shape, data, rotation, centre) -> None:
    dims = data.draw(dims_for(shape))
    obj = make_object(0, shape, "red", dims, centre, rotation)
    if shape == "sphere":
        assert lowest_z(obj) == pytest.approx(centre[2] - dims[0] / 2, abs=1e-12)
        return
    sampled = float(world(obj, surface_samples(shape, dims))[:, 2].min())
    assert lowest_z(obj) <= sampled + 1e-9
    assert sampled - lowest_z(obj) <= 1e-4




# ---------------------------------------------------------------------------------------------
# count and count_attribute


@SETTINGS
@given(scenes(placements=("on", "on", "on", "floor", "stacked")))
def test_count_all_counts_every_object(case) -> None:
    scene, status = case
    on_floor = sorted(i for i, s in status.items() if s == "floor")
    result = outcome("count_all", {}, scene)
    event("floor: reject" if on_floor else "count")
    if on_floor:
        assert result["rejected"] == "off_table" and result["detail"]["object_ids"] == on_floor
        return
    assert result["answer"] == len(scene.objects) == len(result["evidence_object_ids"])
    assert result["shortcut_conflict"] is None


@SETTINGS
@given(scenes(placements=("on", "on", "stacked")))
def test_colour_and_category_counts_sum_to_count_all(case) -> None:
    scene, _ = case
    total = run_program("count_all", {}, scene, CAM).answer
    by_colour = {c: run_program("count_color", {"color": c}, scene, CAM) for c in COLOURS}
    by_shape = {s: run_program("count_category", {"category": s}, scene, CAM) for s in SHAPES}
    assert sum(r.answer for r in by_colour.values()) == total
    assert sum(r.answer for r in by_shape.values()) == total
    for colour, result in by_colour.items():
        assert result.evidence_object_ids == sorted(
            o.id for o in scene.objects if o.color_name == colour
        )


# ---------------------------------------------------------------------------------------------
# translating the whole scene and its cameras in xy changes no answer


def translated(scene: SceneSpec, dx: float, dy: float) -> SceneSpec:
    def move(pose: Pose) -> Pose:
        x, y, z = pose.position
        return Pose(position=(x + dx, y + dy, z), rotation=pose.rotation)

    objects = [o.model_copy(update={"pose": move(o.pose)}) for o in scene.objects]
    cameras = [c.model_copy(update={"pose": move(c.pose)}) for c in scene.cameras]
    g = scene.gripper
    parts = [p.model_copy(update={"pose": move(p.pose)}) for p in g.parts]
    gripper = g.model_copy(update={"tip": move(g.tip), "parts": parts})
    zones = [z.model_copy(update={"center_xy": (z.center_xy[0] + dx, z.center_xy[1] + dy)})
             for z in scene.zones]  # fmt: skip
    return scene.model_copy(
        update={"objects": objects, "cameras": cameras, "gripper": gripper, "zones": zones}
    )


@FEW
@given(case=scenes(placements=("on", "on", "stacked"), min_objects=2),
       shift=st.tuples(st.floats(-0.02, 0.02), st.floats(-0.02, 0.02)))  # fmt: skip
def test_translation_changes_no_answer(case, shift) -> None:
    scene, _ = case
    moved = translated(scene, *shift)
    ids = sorted(o.id for o in scene.objects)
    calls = [("count_all", {}), ("nearest_to_gripper", {})]
    calls += [("count_color", {"color": c}) for c in sorted({o.color_name for o in scene.objects})]
    calls += [("front_to_back", {"a": a, "b": b}) for a, b in zip(ids, ids[1:], strict=False)]
    calls += [("relation", {"anchor": ids[0], "direction": d}) for d in ("left", "right")]
    for name, params in calls:
        before, after = outcome(name, params, scene), outcome(name, params, moved)
        if "rejected" in before or "rejected" in after:
            continue  # a shifted pixel can tip a near tie; the answers themselves must agree
        assert after["answer"] == before["answer"], name


# ---------------------------------------------------------------------------------------------
# nearest_to_gripper


@SETTINGS
@given(scenes(min_objects=2))
def test_nearest_is_no_farther_than_any_other(case) -> None:
    scene, _ = case
    tip = scene.gripper.tip.position
    dist = {o.id: point_distance(o, tip) for o in scene.objects}
    ranked = sorted(dist, key=lambda i: (dist[i], i))
    gap = dist[ranked[1]] - dist[ranked[0]]
    result = outcome("nearest_to_gripper", {}, scene)
    event(result.get("rejected", "accepted"))
    if "rejected" in result:
        assert result["rejected"] in ("near_tie_gripper", "near_tie_pixels", "unnameable")
        if result["rejected"] == "near_tie_gripper":
            assert gap < NEAR_TIE_GRIPPER_FRONT_M + 1e-9
        return
    winner = resolve_object(result["answer"], scene.objects)
    assert winner.ids == (ranked[0],)
    assert result["evidence_object_ids"] == ranked[:2]
    assert result["margin"] == pytest.approx(gap, abs=1e-6)
    assert result["margin"] >= NEAR_TIE_GRIPPER_FRONT_M and result["margin_px"] >= 10
    assert isinstance(result["shortcut_conflict"], bool)


def nearest_case(gap: float, rig: str = "overhead") -> SceneSpec:
    """Two 3 cm spheres 8 cm and 8 cm + gap from the gripper tip."""
    tip = (0.0, 0.0, 1.0)
    a = sphere(0, "red", 0.03, (0.0, 0.0, 1.0 - 0.11))
    b = sphere(1, "blue", 0.03, (0.11 + gap, 0.0, 1.0))
    return make_scene([a, b], tip=tip, rig=rig)


@pytest.mark.parametrize("rig, threshold", [("overhead", 0.03), ("gripper", 0.03), ("front", 0.05)])
@pytest.mark.parametrize("delta, accepted", [(-1e-4, False), (0.0, True), (1e-4, True)])
def test_nearest_threshold_is_exact_per_rig(rig, threshold, delta, accepted) -> None:
    assert {NEAR_TIE_GRIPPER_M, NEAR_TIE_GRIPPER_FRONT_M} == {0.03, 0.05}
    result = outcome("nearest_to_gripper", {}, nearest_case(threshold + delta, rig))
    if accepted:
        assert result["answer"] == "red sphere"
        assert result["margin"] == pytest.approx(threshold + delta, abs=1e-9)
    else:
        assert result["rejected"] == "near_tie_gripper"


def test_naming_rule_rejects_duplicates_and_uses_shortest_name() -> None:
    a = sphere(0, "red", 0.03, (0.0, 0.0, 0.89))
    b = make_object(1, "sphere", "red", (0.06,) * 3, (0.3, 0.0, 0.89))
    scene = make_scene([a, b], tip=(0, 0, 1), rig="overhead")
    assert outcome("nearest_to_gripper", {}, scene)["rejected"] == "unnameable"
    # a duplicate may still be counted
    assert run_program("count_color", {"color": "red"}, scene, CAM).answer == 2
    c = make_object(1, "box", "blue", (0.06,) * 3, (0.3, 0.0, 0.78))
    result = run_program("nearest_to_gripper", {}, make_scene([a, c], tip=(0, 0, 1)), CAM)
    assert result.answer == "sphere"  # the only sphere: the category alone names it
    with pytest.raises(ProgramReject, match="too_few_objects"):
        run_program("nearest_to_gripper", {}, make_scene([a], tip=(0, 0, 1)), CAM)


# ---------------------------------------------------------------------------------------------
# front_to_back


@SETTINGS
@given(case=scenes(min_objects=2), data=st.data())
def test_front_to_back_is_symmetric_and_uses_depth(case, data) -> None:
    scene, _ = case
    a, b = data.draw(st.permutations(scene.objects))[:2]
    ab = outcome("front_to_back", {"a": a.id, "b": b.id}, scene)
    ba = outcome("front_to_back", {"a": b.id, "b": a.id}, scene)
    event(ab.get("rejected", "accepted"))
    if "rejected" in ab:
        assert ab["rejected"] == ba["rejected"]
        return
    assert ab["answer"] == ba["answer"] and ab["margin"] == ba["margin"]
    depth = to_camera(scene.camera(CAM), np.array([a.pose.position, b.pose.position]))[:, 2]
    closer = a if depth[0] < depth[1] else b
    assert resolve_object(ab["answer"], scene.objects).ids == (closer.id,)
    assert ab["margin"] == pytest.approx(abs(depth[0] - depth[1]), abs=1e-6)
    assert ab["margin"] >= NEAR_TIE_DEPTH_FRONT_M


def depth_case(gap: float, lateral: float | None = None, rig: str = "overhead") -> SceneSpec:
    """A camera looking along +y; the red sphere 0.8 m away, the blue one 0.8 m + gap away.
    The nearer sphere sits on the axis and the farther one 5 cm to the side (so the image points
    are apart and the straight-line gap is at least the depth gap), unless `lateral` moves red."""
    eye, target = (0.0, -1.0, 1.0), (0.0, 1.0, 1.0)
    side_a, side_b = (0.0, 0.05) if gap > 0 else (0.05, 0.0)
    if lateral is not None:
        side_a, side_b = lateral, 0.0
    a = sphere(0, "red", 0.02, (side_a, -1.0 + 0.8, 1.0))
    b = sphere(1, "blue", 0.02, (side_b, -1.0 + 0.8 + gap, 1.0))
    return make_scene([a, b], eye=eye, target=target, rig=rig)


@pytest.mark.parametrize("rig, threshold", [("overhead", NEAR_TIE_DEPTH_M),
                                            ("front", NEAR_TIE_DEPTH_FRONT_M)])  # fmt: skip
@pytest.mark.parametrize("delta, accepted", [(-1e-4, False), (0.0, True), (1e-4, True)])
def test_front_to_back_threshold_is_exact_per_rig(rig, threshold, delta, accepted) -> None:
    for sign in (1, -1):
        result = outcome("front_to_back", {"a": "obj_00", "b": "obj_01"},
                         depth_case(sign * (threshold + delta), rig=rig))  # fmt: skip
        if accepted:
            assert result["answer"] == ("red sphere" if sign > 0 else "blue sphere")
        else:
            assert result["rejected"] == "near_tie_depth"


def test_front_to_back_rejects_when_distance_disagrees_with_depth() -> None:
    # The red sphere is 5 cm nearer in planar depth but 60 cm off-axis, so farther in distance.
    with pytest.raises(ProgramReject, match="near_tie_distance"):
        run_program("front_to_back", {"a": "obj_00", "b": "obj_01"}, depth_case(0.05, 0.6), CAM)


# ---------------------------------------------------------------------------------------------
# distance, relation and reach zones against brute force


spread = st.lists(
    st.tuples(st.floats(-0.35, 0.35), st.floats(-0.22, 0.22)), min_size=3, max_size=6
).filter(lambda ps: all(math.dist(p, q) > 0.07 for i, p in enumerate(ps) for q in ps[i + 1:]))


def flat_scene(points, **kw) -> SceneSpec:
    pairs = [(c, s) for s in ("box", "cylinder", "sphere") for c in COLOURS][: len(points)]
    objs = []
    for i, ((x, y), (colour, shape)) in enumerate(zip(points, pairs, strict=True)):
        objs.append(make_object(i, shape, colour, (0.04, 0.04, 0.04), (x, y, TABLE_H + 0.02)))
    return make_scene(objs, **kw)


@FEW
@given(points=spread)
def test_distance_cm_matches_brute_force(points) -> None:
    scene = flat_scene(points, grid=0.05)
    a, b = scene.objects[0], scene.objects[1]
    d = math.dist(points[0], points[1])
    result = outcome("distance_cm", {"a": a.id, "b": b.id}, scene)
    if d < 0.08:
        assert result["rejected"] == "too_close"
        return
    assert result["answer"] == round(d * 100, 1)
    assert result["tolerance"] == pytest.approx(max(2.0, 0.1 * result["answer"]), abs=0.01)
    assert result["answer_type"] == "number"
    no_grid = flat_scene(points)
    assert outcome("distance_cm", {"a": a.id, "b": b.id}, no_grid)["rejected"] == \
        "no_scale_in_frame"


@FEW
@given(points=spread, direction=st.sampled_from(("left", "right")))
def test_relation_matches_brute_force(points, direction) -> None:
    scene = flat_scene(points)
    right = camera_right_xy(scene.camera(CAM))
    want = right if direction == "right" else -right
    anchor = np.array(points[0])
    rows = []
    for i, p in enumerate(points[1:], start=1):
        d = np.array(p) - anchor
        angle = math.degrees(math.acos(np.clip(d @ want / np.linalg.norm(d), -1, 1)))
        rows.append((float(np.linalg.norm(d)), angle, f"obj_{i:02d}"))
    cands = sorted(r for r in rows if r[1] <= 30)
    result = outcome("relation", {"anchor": "obj_00", "direction": direction}, scene)
    event(result.get("rejected", "accepted"))
    if not cands:
        assert result["rejected"] == "no_candidate"
        return
    if "rejected" in result:
        assert result["rejected"] in ("near_tie_relation", "near_cone_edge", "near_tie_pixels")
        return
    assert resolve_object(result["answer"], scene.objects).ids == (cands[0][2],)
    assert all(not (25 <= a <= 35 and dd < cands[0][0]) for dd, a, _ in rows)
    if len(cands) > 1:
        assert result["margin"] == pytest.approx(cands[1][0] - cands[0][0], abs=1e-6) and \
            result["margin"] >= 0.03


@FEW
@given(points=spread, centre=st.tuples(st.floats(-0.2, 0.2), st.floats(-0.1, 0.1)),
       radius=st.floats(0.08, 0.2))  # fmt: skip
def test_in_zone_matches_brute_force(points, centre, radius) -> None:
    zone = Zone(id="zone_0", shape="circle", center_xy=centre, radius=radius, color_name="green")
    scene = flat_scene(points, zones=[zone])
    sd = [radius - math.dist(p, centre) for p in points]
    result = outcome("in_zone", {"zone": "zone_0"}, scene)
    if any(abs(s) < 0.02 for s in sd):
        assert result["rejected"] == "near_zone_boundary"
        return
    event(result.get("rejected", "accepted"))
    if "rejected" in result:
        assert result["rejected"] == "near_zone_boundary"  # pixel margin
        return
    members = sorted(f"obj_{i:02d}" for i, s in enumerate(sd) if s >= 0)
    got = sorted(i for name in result["answer"] for i in resolve_object(name, scene.objects).ids)
    assert got == members
    assert result["answer_text"] == (", ".join(result["answer"]) or "none")
    assert result["margin"] == pytest.approx(min(abs(s) for s in sd), abs=1e-6)


# ---------------------------------------------------------------------------------------------
# run_program contract


def test_run_program_contract() -> None:
    scene = read_scene(FIXTURE)
    assert PROGRAMS_VERSION == "0.2.2"
    assert set(PROGRAMS) == set(PROGRAM_FAMILY)
    assert all(PROGRAMS[p].family in FAMILIES for p in PROGRAMS)
    unsettled = scene.model_copy(update={"meta": scene.meta.model_copy(update={"settled": False})})
    with pytest.raises(ValueError, match="settled"):
        run_program("count_all", {}, unsettled, CAM)
    with pytest.raises(ValueError, match="unknown program"):
        run_program("count_everything", {}, scene, CAM)
    with pytest.raises(ValueError, match="params"):
        run_program("count_all", {"color": "red"}, scene, CAM)
    with pytest.raises(ValueError, match="different"):
        run_program("front_to_back", {"a": "obj_01", "b": "obj_01"}, scene, CAM)
    with pytest.raises(ProgramReject, match="wrong_rig"):
        run_program("gripper_center", {}, scene, CAM)
    with pytest.raises(KeyError):
        run_program("count_all", {}, scene, "overhead")


def test_fixture_tasks_match_programs() -> None:
    """What G1 does on the v0.1 fixture: the rerun names the same object (v0.2 names are the
    shortest unique form, so 'red box' may now be 'box')."""
    scene = read_scene(FIXTURE)
    for path in sorted((RUN / "tasks").glob("*/task.json")):
        task = read_task(path)
        result = run_program(task.program.name, task.program.params, scene, task.camera_id)
        if task.answer_type == "object":
            assert resolve_object(result.answer, scene.objects).ids == \
                resolve_object(task.answer, scene.objects).ids, path
        else:
            assert result.answer == task.answer, path
        assert set(task.evidence_object_ids) <= set(result.evidence_object_ids) | set(
            o.id for o in scene.objects
        ), path
