"""Question programs v0: property tests on count, nearest_to_gripper and front_to_back, the shape
geometry they rest on, and compile_questions on the contract fixture.

Scenes are built in-test, so these tests do not depend on the sampler lane.
"""

import math
from pathlib import Path

import numpy as np
import pytest
from hypothesis import HealthCheck, assume, event, given, settings
from hypothesis import strategies as st

from vtf.core.geometry import Pose, look_at_camera_quat, quat_from_axis_angle, quat_multiply
from vtf.core.io import read_scene, read_task
from vtf.core.program import CompiledQuestion, ProgramReject
from vtf.core.projection import to_camera
from vtf.core.scene import (
    CameraSpec,
    Lighting,
    SceneMeta,
    SceneObject,
    SceneSpec,
    Table,
    build_gripper,
)
from vtf.core.vocab import COLOURS, SHAPES, linear_rgb, normalise_object
from vtf.programs import (
    NEAR_TIE_DEPTH_M,
    NEAR_TIE_GRIPPER_M,
    PROGRAMS,
    PROGRAMS_VERSION,
    TABLE_CONTACT_TOL_M,
    compile_questions,
    run_program,
)
from vtf.programs.shapes import lowest_z, point_distance

REPO = Path(__file__).resolve().parents[1]
RUN = REPO / "spec" / "examples" / "run_example"
FIXTURE = RUN / "scenes" / "scene_0000" / "render" / "scene.settled.json"
TABLE_H = 0.75
CAM = "front"
SETTINGS = settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])


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
) -> SceneSpec:
    return SceneSpec(
        meta=SceneMeta(
            scene_id="scene_test",
            generator_version="test",
            seed=0,
            recipe_hash="0" * 64,
            settled=settled,
        ),
        table=Table(size_xy=(1.0, 0.7), height=TABLE_H),
        objects=list(objects),
        gripper=build_gripper(Pose(position=tuple(tip))),
        cameras=[
            CameraSpec(
                id=CAM,
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
# count


@SETTINGS
@given(scenes(placements=("on", "on", "on", "floor", "stacked")))
def test_count_all_counts_on_table_objects(case) -> None:
    scene, status = case
    stacked = sorted(i for i, s in status.items() if s == "stacked")
    event("stacked: reject" if stacked else "no stacked: count")
    if stacked:
        with pytest.raises(ProgramReject) as err:
            run_program("count_all", {}, scene, CAM)
        assert err.value.reason == "ambiguous_support"
        assert err.value.detail["object_ids"] == stacked
        return
    result = run_program("count_all", {}, scene, CAM)
    on_table = sorted(i for i, s in status.items() if s == "on")
    assert result.answer == len(on_table)
    assert result.answer_type == "int" and result.answer_text == str(len(on_table))
    assert result.evidence_object_ids == on_table
    rows = {r["object_id"]: r for r in result.trace if "object_id" in r}
    assert set(rows) == set(status)
    for oid, where in status.items():
        assert rows[oid]["status"] == {"on": "on_table", "floor": "off_table"}[where]


@SETTINGS
@given(scenes(placements=("on", "on", "on", "floor")))
def test_colour_counts_sum_to_count_all(case) -> None:
    scene, status = case
    total = run_program("count_all", {}, scene, CAM).answer
    per_colour = {c: run_program("count_color", {"color": c}, scene, CAM) for c in COLOURS}
    assert sum(r.answer for r in per_colour.values()) == total
    for colour, result in per_colour.items():
        expected = sorted(
            o.id for o in scene.objects if o.color_name == colour and status[o.id] == "on"
        )
        assert result.answer == len(expected)
        assert result.evidence_object_ids == expected


@pytest.mark.parametrize(
    "lift, accepted",
    [(TABLE_CONTACT_TOL_M, True), (-TABLE_CONTACT_TOL_M, True), (0.0101, False), (-0.0101, False)],
)
def test_on_table_tolerance_is_exact(lift, accepted) -> None:
    box = make_object(0, "box", "red", (0.06, 0.06, 0.06), (0.0, 0.0, TABLE_H + 0.03 + lift))
    scene = make_scene([box])
    if accepted:
        assert run_program("count_all", {}, scene, CAM).answer == 1
    else:
        with pytest.raises(ProgramReject, match="ambiguous_support"):
            run_program("count_all", {}, scene, CAM)


def test_tilted_box_rests_on_its_edge() -> None:
    # A 4 x 8 x 6 cm box rolled 45 degrees about x rests on an edge, its centre
    # (8 + 6) / 2 * sin 45 = 4.95 cm above the top.
    rotation = quat_from_axis_angle((1.0, 0.0, 0.0), math.pi / 4)
    box = make_object(0, "box", "red", (0.04, 0.08, 0.06), (0.1, 0.1, 0.0), rotation)
    drop = 0.07 * math.sqrt(0.5)
    assert lowest_z(box) == pytest.approx(-drop, abs=1e-12)
    box = box.model_copy(
        update={"pose": Pose(position=(0.1, 0.1, TABLE_H + drop), rotation=rotation)}
    )
    assert run_program("count_all", {}, make_scene([box]), CAM).answer == 1


# ---------------------------------------------------------------------------------------------
# shuffling the object list changes nothing


@SETTINGS
@given(case=scenes(placements=("on", "on", "floor", "stacked"), min_objects=2), data=st.data())
def test_shuffling_objects_changes_no_answer(case, data) -> None:
    scene, _ = case
    shuffled = scene.model_copy(update={"objects": data.draw(st.permutations(scene.objects))})
    ids = sorted(o.id for o in scene.objects)
    calls = [("count_all", {}), ("nearest_to_gripper", {})]
    calls += [("count_color", {"color": c}) for c in sorted({o.color_name for o in scene.objects})]
    calls += [("front_to_back", {"a": a, "b": b}) for a, b in zip(ids, ids[1:], strict=False)]
    for name, params in calls:
        assert outcome(name, params, shuffled) == outcome(name, params, scene), name
    seed = data.draw(st.integers(0, 2**32))
    original = [q.model_dump() for q in compile_questions(scene, CAM, seed)]
    assert [q.model_dump() for q in compile_questions(shuffled, CAM, seed)] == original


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
    try:
        result = run_program("nearest_to_gripper", {}, scene, CAM)
    except ProgramReject as rej:
        event(rej.reason)
        assert rej.reason == "near_tie_gripper"
        assert gap < NEAR_TIE_GRIPPER_M + 1e-9
        assert rej.detail["nearest"] == ranked[0] and rej.detail["runner_up"] == ranked[1]
        return
    event("accepted")
    winner = next(o for o in scene.objects if o.name == result.answer)
    assert all(dist[winner.id] <= d for d in dist.values())
    assert result.evidence_object_ids == ranked[:2]
    assert result.margin == pytest.approx(gap, abs=1e-6)
    assert result.margin >= NEAR_TIE_GRIPPER_M
    assert normalise_object(result.answer) == result.answer


def nearest_case(gap: float) -> SceneSpec:
    """Two 3 cm spheres 8 cm and 8 cm + gap from the gripper tip."""
    tip = (0.0, 0.0, 1.0)
    a = sphere(0, "red", 0.03, (0.0, 0.0, 1.0 - 0.11))
    b = sphere(1, "blue", 0.03, (0.11 + gap, 0.0, 1.0))
    return make_scene([a, b], tip=tip)


@pytest.mark.parametrize(
    "gap, accepted", [(0.03 - 1e-4, False), (0.03, True), (0.03 + 1e-4, True), (0.0, False)]
)
def test_nearest_threshold_is_exact(gap, accepted) -> None:
    scene = nearest_case(gap)
    if accepted:
        result = run_program("nearest_to_gripper", {}, scene, CAM)
        assert result.answer == "red sphere"
        assert result.margin == pytest.approx(gap, abs=1e-9)
    else:
        with pytest.raises(ProgramReject, match="near_tie_gripper"):
            run_program("nearest_to_gripper", {}, scene, CAM)


@SETTINGS
@given(st.floats(0.0, 0.08))
def test_nearest_rejects_exactly_below_threshold(gap) -> None:
    assume(abs(gap - NEAR_TIE_GRIPPER_M) > 1e-7)
    rejected = outcome("nearest_to_gripper", {}, nearest_case(gap)).get("rejected")
    assert (rejected == "near_tie_gripper") == (gap < NEAR_TIE_GRIPPER_M)


def test_nearest_rejects_duplicate_and_single() -> None:
    a = sphere(0, "red", 0.03, (0.0, 0.0, 0.89))
    b = make_object(1, "sphere", "red", (0.06,) * 3, (0.3, 0.0, 0.89))
    with pytest.raises(ProgramReject, match="duplicate_name"):
        run_program("nearest_to_gripper", {}, make_scene([a, b], tip=(0, 0, 1)), CAM)
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
    assert ab["evidence_object_ids"] == [a.id, b.id] == ba["evidence_object_ids"][::-1]
    depth = to_camera(scene.camera(CAM), np.array([a.pose.position, b.pose.position]))[:, 2]
    assert ab["answer"] == (a if depth[0] < depth[1] else b).name
    assert ab["margin"] == pytest.approx(abs(depth[0] - depth[1]), abs=1e-6)
    assert ab["margin"] >= NEAR_TIE_DEPTH_M


def depth_case(gap: float, lateral: float = 0.0) -> SceneSpec:
    """A camera looking along +y; one sphere 0.8 m away on the axis, one 0.8 m + gap away."""
    eye, target = (0.0, -1.0, 1.0), (0.0, 1.0, 1.0)
    a = sphere(0, "red", 0.02, (lateral, -1.0 + 0.8, 1.0))
    b = sphere(1, "blue", 0.02, (0.0, -1.0 + 0.8 + gap, 1.0))
    return make_scene([a, b], eye=eye, target=target)


@pytest.mark.parametrize(
    "gap, accepted", [(0.04 - 1e-4, False), (0.04, True), (0.04 + 1e-4, True), (-0.04, True)]
)
def test_front_to_back_threshold_is_exact(gap, accepted) -> None:
    scene = depth_case(gap)
    if accepted:
        result = run_program("front_to_back", {"a": "obj_00", "b": "obj_01"}, scene, CAM)
        assert result.answer == ("red sphere" if gap > 0 else "blue sphere")
        assert result.margin == pytest.approx(abs(gap), abs=1e-9)
    else:
        with pytest.raises(ProgramReject, match="near_tie_depth"):
            run_program("front_to_back", {"a": "obj_00", "b": "obj_01"}, scene, CAM)


@SETTINGS
@given(st.floats(-0.1, 0.1))
def test_front_to_back_rejects_exactly_below_threshold(gap) -> None:
    assume(abs(abs(gap) - NEAR_TIE_DEPTH_M) > 1e-7)
    rejected = outcome("front_to_back", {"a": "obj_00", "b": "obj_01"}, depth_case(gap))
    assert (rejected.get("rejected") == "near_tie_depth") == (abs(gap) < NEAR_TIE_DEPTH_M)


def test_front_to_back_rejects_when_distance_disagrees_with_depth() -> None:
    # The red sphere is 5 cm nearer in planar depth but 60 cm off-axis, so farther in distance.
    scene = depth_case(0.05, lateral=0.6)
    with pytest.raises(ProgramReject, match="near_tie_distance"):
        run_program("front_to_back", {"a": "obj_00", "b": "obj_01"}, scene, CAM)


# ---------------------------------------------------------------------------------------------
# run_program contract


def test_run_program_contract() -> None:
    scene = read_scene(FIXTURE)
    assert PROGRAMS_VERSION == "0.1.0"
    assert set(PROGRAMS) == {"count_all", "count_color", "nearest_to_gripper", "front_to_back"}
    unsettled = scene.model_copy(update={"meta": scene.meta.model_copy(update={"settled": False})})
    with pytest.raises(ValueError, match="settled"):
        run_program("count_all", {}, unsettled, CAM)
    with pytest.raises(ValueError, match="settled"):
        compile_questions(unsettled, CAM, 0)
    with pytest.raises(ValueError, match="unknown program"):
        run_program("count_everything", {}, scene, CAM)
    with pytest.raises(ValueError, match="params"):
        run_program("count_all", {"color": "red"}, scene, CAM)
    with pytest.raises(ValueError, match="different"):
        run_program("front_to_back", {"a": "obj_01", "b": "obj_01"}, scene, CAM)
    with pytest.raises(KeyError):
        run_program("count_all", {}, scene, "overhead")


def test_fixture_tasks_match_programs() -> None:
    """What G1 will do: re-run each stored program on the settled scene and compare."""
    scene = read_scene(FIXTURE)
    for path in sorted((RUN / "tasks").glob("*/task.json")):
        task = read_task(path)
        result = run_program(task.program.name, task.program.params, scene, task.camera_id)
        assert result.answer == task.answer, path
        assert sorted(result.evidence_object_ids) == sorted(task.evidence_object_ids), path


# ---------------------------------------------------------------------------------------------
# compile_questions


def test_compile_questions_on_fixture() -> None:
    scene = read_scene(FIXTURE)
    questions = compile_questions(scene, CAM, seed=0)
    accepted = [q for q in questions if q.rejected is None]
    assert 3 <= len(accepted) <= 5
    assert {q.family for q in accepted} == {"count", "nearest_to_gripper", "front_to_back"}
    answers = {
        q.program.name: q.result.answer for q in accepted if q.program.name != "front_to_back"
    }
    assert answers == {"count_all": 7, "count_color": 2, "nearest_to_gripper": "blue cylinder"}
    for q in accepted:
        assert q.result == run_program(q.program.name, q.program.params, scene, CAM)
        assert CompiledQuestion.model_validate_json(q.model_dump_json()) == q
    again = compile_questions(scene, CAM, seed=0)
    assert [q.model_dump() for q in again] == [q.model_dump() for q in questions]


@SETTINGS
@given(
    case=scenes(placements=("on", "on", "on", "stacked")),
    seed=st.integers(0, 2**32),
    max_questions=st.one_of(st.just(5), st.integers(1, 6)),
)
def test_compile_questions_properties(case, seed, max_questions) -> None:
    scene, _ = case
    questions = compile_questions(scene, CAM, seed, max_questions)
    assert [q.model_dump() for q in compile_questions(scene, CAM, seed, max_questions)] == [
        q.model_dump() for q in questions
    ]
    accepted = [q for q in questions if q.rejected is None]
    assert len(accepted) <= max_questions
    assert sum(q.program.name == "front_to_back" for q in accepted) <= 2
    assert sum(q.program.name == "front_to_back" for q in questions) <= 3
    event(f"{len(accepted)} accepted, {len(questions) - len(accepted)} rejected")
    for q in questions:
        assert q.template_id in {f"{q.program.name}.p1", f"{q.program.name}.p2"}
        assert q.family == PROGRAMS[q.program.name].family
        assert (q.result is None) == (q.rejected is not None)
        expected = outcome(q.program.name, q.program.params, scene)
        if q.rejected:
            assert expected == {"rejected": q.rejected, "detail": q.rejected_detail}
        else:
            assert expected == q.result.model_dump()
        CompiledQuestion.model_validate_json(q.model_dump_json())
