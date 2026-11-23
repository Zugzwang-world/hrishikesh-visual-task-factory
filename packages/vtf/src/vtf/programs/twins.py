"""make_twin: each family's counterfactual twin rule (G6, spec/families.md).

A twin moves one evidence object (or, for counts, removes one counted object) so the answer must
change. Candidate edits are tried in a seeded order: the moved object keeps its rotation and goes
to a collision-free spot on the table (1.5 cm clear of every footprint, 4 cm inside the table
edge, clear of the gripper, inside the task camera's frame), onto a flat top, or into a
container. Each candidate is checked by running the program on the edited scene with the object
at rest (treated as settled): the edit is kept only if the program accepts, the answer differs
from the original and the evidence stays visible (G3 thresholds). `expected_answer` is that
answer; `new_pose` is the rest pose lifted SPAWN_LIFT_M so the twin's settle drops it into place.
Returns None when no candidate works.
"""

import math
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np

from vtf.core.geometry import Pose, round_vec
from vtf.core.hashing import sha256_json
from vtf.core.program import PROGRAM_FAMILY, ProgramReject, ProgramResult, TwinEdit
from vtf.core.rng import make_rng
from vtf.core.scene import SceneSpec
from vtf.programs.geometry import (
    ObjGeom,
    SceneGeom,
    convex_hull,
    diameter,
    min_width,
    point_in_polygon,
    polygon_distance,
    scene_geom,
)
from vtf.programs.library import (
    DISTANCE_MIN_M,
    NEAR_TIE_DEPTH_FRONT_M,
    NEAR_TIE_DEPTH_M,
    run_program,
    zone_signed_distance,
)
from vtf.programs.view import camera_view, hidden_evidence, planar_depth

SPAWN_LIFT_M = 0.01
CLEARANCE_M = 0.015
TABLE_MARGIN_M = 0.04
GRID_STEP_M = 0.015
GRIPPER_CLEARANCE_M = 0.01
FRAME_MARGIN_PX = 16.0
MAX_CHECKS = 10  # program runs per twin
MAX_GRID_TESTS = 600  # table spots examined per call
NEAREST_TWIN_M = 0.15  # nearest_to_gripper: at least 15 cm farther than the runner-up
OCCLUSION_AWAY_M = 0.15
GRIPPER_AWAY_M = 0.12


@dataclass(frozen=True)
class Edit:
    object_id: str
    position: tuple[float, float, float] | None  # rest position (None: remove)
    reason: str

    @property
    def remove(self) -> bool:
        return self.position is None


class _Twin:
    def __init__(self, name: str, params: dict, scene: SceneSpec, camera_id: str,
                 original: ProgramResult, rng: np.random.Generator) -> None:  # fmt: skip
        self.name, self.params, self.scene = name, params, scene
        self.cam = scene.camera(camera_id)
        self.original = original
        self.rng = rng
        self.sg: SceneGeom = scene_geom(scene)
        self.family = PROGRAM_FAMILY[name]
        sx, sy = scene.table.size_xy
        self.half = (sx / 2 - TABLE_MARGIN_M, sy / 2 - TABLE_MARGIN_M)
        parts = []
        for p in scene.gripper.parts:
            r = max(p.dimensions) / 2
            parts += [(p.pose.position[0] + dx, p.pose.position[1] + dy)
                      for dx in (-r, r) for dy in (-r, r)]  # fmt: skip
        self.gripper_hull = convex_hull(np.array(parts))
        self.gripper_low = min(p.pose.position[2] - max(p.dimensions) / 2
                               for p in scene.gripper.parts)  # fmt: skip

    # -- placement ------------------------------------------------------------------------

    def shuffled(self, items: list) -> list:
        return [items[int(i)] for i in self.rng.permutation(len(items))] if items else []

    def movable(self, oid: str) -> bool:
        """Objects that carry or hold others stay put (moving them would move a whole stack)."""
        if self.sg.supported_by(oid):
            return False
        return not (self.sg.geom[oid].container and self.sg.contents(oid))

    def rest_z_on_table(self, g: ObjGeom) -> float:
        return float(self.scene.table.height + (g.centre[2] - g.lowest_z))

    def spot_ok(self, oid: str, xy, z: float, ignore: tuple[str, ...] = (),
                frame: bool = True) -> bool:
        g = self.sg.geom[oid]
        hull = g.hull - g.xy + np.asarray(xy)
        r_obj = float(np.linalg.norm(g.hull - g.xy, axis=1).max())
        for other in self.sg.objects:
            if other.id == oid or other.id in ignore:
                continue
            og = self.sg.geom[other.id]
            r_o = float(np.linalg.norm(og.hull - og.xy, axis=1).max())
            if np.linalg.norm(og.xy - xy) >= r_obj + r_o + CLEARANCE_M:
                continue
            if polygon_distance(hull, og.hull) < CLEARANCE_M:
                return False
        top = z + (g.top_z - g.centre[2])
        if top > self.gripper_low - GRIPPER_CLEARANCE_M and \
                polygon_distance(hull, self.gripper_hull) < CLEARANCE_M:
            return False
        return not frame or self.in_frame(g, (xy[0], xy[1], z))

    def in_frame(self, g: ObjGeom, position) -> bool:
        """The moved object's footprint prism (hull at its lowest and top z) is in the image."""
        from vtf.core.projection import project

        dx, dy, dz = np.asarray(position) - g.centre
        hull = g.hull + (dx, dy)
        verts = np.vstack([np.c_[hull, np.full(len(hull), z + dz)] for z in (g.lowest_z, g.top_z)])
        uv, depth = project(self.cam, verts)
        w, h = self.cam.resolution
        m = FRAME_MARGIN_PX
        return bool((depth > self.cam.clip_start).all() and (uv[:, 0] >= m).all()
                    and (uv[:, 0] <= w - m).all() and (uv[:, 1] >= m).all()
                    and (uv[:, 1] <= h - m).all())  # fmt: skip

    def named(self, oid: str) -> bool:
        """Objects the question names must stay in the camera's frame when moved."""
        return oid in {str(v) for v in self.params.values()}

    def table_spots(self, oid: str, keep=None, limit: int = MAX_CHECKS,
                    order=None) -> list[tuple[float, float, float]]:  # fmt: skip
        """Free table spots for an object, filtered by `keep(xy)`, seeded order or `order`."""
        g = self.sg.geom[oid]
        r = float(np.linalg.norm(g.hull - g.xy, axis=1).max())
        xs = np.arange(-self.half[0] + r, self.half[0] - r + 1e-9, GRID_STEP_M)
        ys = np.arange(-self.half[1] + r, self.half[1] - r + 1e-9, GRID_STEP_M)
        grid = [np.array((float(x), float(y))) for x in xs for y in ys]
        grid = [p for p in grid if np.linalg.norm(p - g.xy) > 0.02 and (keep is None or keep(p))]
        if order is None:
            grid = [grid[int(i)] for i in self.rng.permutation(len(grid))] if grid else []
        else:
            grid.sort(key=lambda p: (order(p), p[0], p[1]))
        z = self.rest_z_on_table(g)
        out = []
        frame = self.named(oid)
        for p in grid[:MAX_GRID_TESTS]:
            if self.spot_ok(oid, p, z, frame=frame):
                out.append((round(float(p[0]), 6), round(float(p[1]), 6), round(z, 6)))
                if len(out) >= limit:
                    break
        return out

    def onto(self, oid: str, target: str, offset=(0.0, 0.0)) -> tuple[float, float, float] | None:
        """Rest position of `oid` on `target`'s top surface or interior floor (centre + offset)."""
        g, tg = self.sg.geom[oid], self.sg.geom[target]
        x, y = float(tg.xy[0] + offset[0]), float(tg.xy[1] + offset[1])
        surface = tg.surface_below(x, y, tg.top_z + 0.01)
        if surface is None:
            return None
        z = surface + (g.centre[2] - g.lowest_z)
        if z + (g.top_z - g.centre[2]) > self.gripper_low - GRIPPER_CLEARANCE_M and \
                point_in_polygon((x, y), self.gripper_hull):
            return None
        if not self.spot_ok(oid, (x, y), z, ignore=(target,), frame=self.named(oid)):
            return None
        return (round(x, 6), round(y, 6), round(float(z), 6))

    # -- checking -------------------------------------------------------------------------

    def edited(self, edit: Edit, lift: float = 0.0) -> SceneSpec:
        if edit.remove:
            objects = [o for o in self.scene.objects if o.id != edit.object_id]
        else:
            objects = []
            for o in self.scene.objects:
                if o.id == edit.object_id:
                    x, y, z = edit.position  # type: ignore[misc]
                    pose = Pose(position=round_vec((x, y, z + lift)), rotation=o.pose.rotation)  # type: ignore[arg-type]
                    o = o.model_copy(update={"pose": pose})
                objects.append(o)
        return self.scene.model_copy(update={"objects": objects})

    def check(self, edit: Edit) -> TwinEdit | None:
        twin = self.edited(edit)
        try:
            result = run_program(self.name, self.params, twin, self.cam.id)
        except ProgramReject:
            return None
        if sha256_json(result.answer) == sha256_json(self.original.answer):
            return None
        view = camera_view(twin, self.cam.id)
        if hidden_evidence(view, result.evidence_object_ids, self.family, self.params):
            return None
        if edit.remove:
            return TwinEdit(object_id=edit.object_id, remove=True,
                            expected_answer=result.answer, reason=edit.reason)  # fmt: skip
        moved = self.edited(edit, lift=SPAWN_LIFT_M).object(edit.object_id)
        return TwinEdit(object_id=edit.object_id, new_pose=moved.pose,
                        expected_answer=result.answer, reason=edit.reason)  # fmt: skip

    # -- family rules (each yields candidate edits) ---------------------------------------

    def candidates(self) -> Iterator[Edit]:
        rule = getattr(self, f"rule_{self.family}")
        yield from rule()

    def _remove_counted(self) -> Iterator[Edit]:
        counted = [i for i in self.original.evidence_object_ids
                   if i != self.params.get("container")]  # fmt: skip
        if self.name == "count_color_in":
            return
        order = [counted[int(i)] for i in self.rng.permutation(len(counted))] if counted else []
        for oid in sorted(order, key=lambda i: not self.movable(i)):
            yield Edit(oid, None, "remove one counted object")

    def rule_count(self) -> Iterator[Edit]:
        yield from self._remove_counted()

    def rule_count_attribute(self) -> Iterator[Edit]:
        if self.name == "count_color":
            yield from self._remove_counted()
            return
        cid = self.params["container"]
        inside = [i for i in self.original.evidence_object_ids if i != cid]
        for oid in inside:
            if self.movable(oid):
                for spot in self.table_spots(oid, limit=2):
                    yield Edit(oid, spot, "move one counted object out of the container")
        if not inside:
            colour = self.params["color"]
            for o in self.sg.objects:
                if o.color_name == colour and o.id != cid and self.movable(o.id):
                    spot = self.into(o.id, cid)
                    if spot:
                        yield Edit(o.id, spot, "move an object of that colour into the container")

    def into(self, oid: str, container_id: str):
        """A rest position inside the container: its centre, else a ring of spots inside."""
        g, cg = self.sg.geom[oid], self.sg.geom[container_id]
        info = cg.container
        room = (2 * (info.inner_radius_m or 0.0) if info.shape == "circle"
                else min(info.inner_size_xy or (0.0, 0.0))) * cg.obj.scale  # fmt: skip
        size = diameter(g.hull)
        if size > room - 0.02:
            return None
        ring = room / 2 - size / 2 - 0.012
        offsets = [(0.0, 0.0)] + [(ring * math.cos(a), ring * math.sin(a))
                                  for a in np.linspace(0, 2 * math.pi, 8, endpoint=False)]
        for off in offsets if ring > 0 else offsets[:1]:
            spot = self.onto(oid, container_id, off)
            if spot and self._inside_after(oid, container_id, spot):
                return spot
        return None

    def _inside_after(self, oid: str, container_id: str, spot) -> bool:
        sg = scene_geom(self.edited(Edit(oid, spot, "")))
        return sg.inside(oid, container_id).inside

    def rule_nearest_to_gripper(self) -> Iterator[Edit]:
        win, run = self.original.evidence_object_ids[:2]
        tip = np.asarray(self.scene.gripper.tip.position)
        d_run = self.sg.geom[run].surface_distance(tip)
        g = self.sg.geom[win]
        r = float(np.linalg.norm(g.hull - g.xy, axis=1).max())
        if not self.movable(win):
            return
        far = d_run + NEAREST_TWIN_M
        for spot in self.table_spots(win, keep=lambda p: np.linalg.norm(p - tip[:2]) - r >= far):
            yield Edit(win, spot, "move the nearest object 15 cm farther than the runner-up")

    def rule_distance(self) -> Iterator[Edit]:
        d = self.original.answer / 100.0  # type: ignore[operator]
        step = 3 * (self.original.tolerance or 2.0) / 100.0 + 0.01
        for fixed, moved in ((self.params["a"], self.params["b"]),
                             (self.params["b"], self.params["a"])):
            if not self.movable(moved):
                continue
            gf = self.sg.geom[fixed]

            def keep(p, gf=gf):
                nd = float(np.linalg.norm(p - gf.xy))
                return abs(nd - d) > step and nd >= DISTANCE_MIN_M + 0.02

            for spot in self.table_spots(moved, keep=keep, limit=5):
                yield Edit(moved, spot, "move one object so the distance changes by more than "
                           "three tolerances")
            return

    def rule_front_to_back(self) -> Iterator[Edit]:
        a, b = self.params["a"], self.params["b"]
        depth = {i: planar_depth(self.cam, self.sg.geom[i].centre) for i in (a, b)}
        near, far = (a, b) if depth[a] < depth[b] else (b, a)
        thr = NEAR_TIE_DEPTH_FRONT_M if self.cam.rig == "front" else NEAR_TIE_DEPTH_M
        if self.movable(far):
            z = self.rest_z_on_table(self.sg.geom[far])

            def in_front(p, z=z):
                return planar_depth(self.cam, (p[0], p[1], z)) < depth[near] - thr - 0.03

            for spot in self.table_spots(far, keep=in_front, limit=5):
                yield Edit(far, spot, "move the farther object in front of the nearer one")
        if self.movable(near):  # fallback: the nearer one goes behind the farther one
            z = self.rest_z_on_table(self.sg.geom[near])

            def behind(p, z=z):
                return planar_depth(self.cam, (p[0], p[1], z)) > depth[far] + thr + 0.03

            for spot in self.table_spots(near, keep=behind, limit=5):
                yield Edit(near, spot, "move the nearer object behind the farther one")

    def rule_relation(self) -> Iterator[Edit]:
        from vtf.programs.view import camera_right_xy

        anchor, ans = self.params["anchor"], self.original.evidence_object_ids[1]
        if not self.movable(ans):
            return
        right = camera_right_xy(self.cam)
        opposite = -right if self.params["direction"] == "right" else right
        ga, g = self.sg.geom[anchor], self.sg.geom[ans]
        dist = float(np.linalg.norm(g.xy - ga.xy))

        def keep(p):
            d = p - ga.xy
            n = float(np.linalg.norm(d))
            return n > 0.05 and float(d @ opposite) / n >= math.cos(math.radians(20))

        for spot in self.table_spots(ans, keep=keep, limit=4,
                                     order=lambda p: abs(np.linalg.norm(p - ga.xy) - dist)):
            yield Edit(ans, spot, "move the answer to the opposite side of the anchor")
        # fallback: bring another object into the asked direction, nearer than the answer
        want = -opposite
        others = [o.id for o in self.sg.objects if o.id not in (anchor, ans)
                  and self.movable(o.id) and self.sg.name(o.id)]
        for oid in self.shuffled(others)[:3]:
            def closer(p):
                d = p - ga.xy
                n = float(np.linalg.norm(d))
                return 0.05 < n < dist - 0.05 and float(d @ want) / n >= math.cos(math.radians(15))

            for spot in self.table_spots(oid, keep=closer, limit=2,
                                         order=lambda p: np.linalg.norm(p - ga.xy)):
                yield Edit(oid, spot, "move another object into the asked direction, nearer")

    def rule_support(self) -> Iterator[Edit]:
        a = self.params["a"]
        if not self.movable(a):
            return
        if self.original.answer != "table":
            for spot in self.table_spots(a, limit=4):
                yield Edit(a, spot, "move the object off its support onto the table")
            return
        g = self.sg.geom[a]
        if not _stackable(g):
            return
        targets = [o.id for o in self.sg.objects if o.id != a and _flat_top(self.sg.geom[o.id])
                   and not self.sg.supported_by(o.id)
                   and diameter(g.hull) <= min_width(self.sg.geom[o.id].hull) + 0.02]
        for t in [targets[int(i)] for i in self.rng.permutation(len(targets))] if targets else []:
            spot = self.onto(a, t)
            if spot:
                yield Edit(a, spot, "move the object onto a flat-topped object")

    def rule_containment(self) -> Iterator[Edit]:
        cid = self.params["container"]
        members = [i for i in self.original.evidence_object_ids if i != cid]
        for oid in members:
            if self.movable(oid):
                for spot in self.table_spots(oid, limit=2):
                    yield Edit(oid, spot, "move one contained object out onto the table")
        if not members:
            small = sorted((o.id for o in self.sg.objects if o.id != cid and self.movable(o.id)),
                           key=lambda i: diameter(self.sg.geom[i].hull))  # fmt: skip
            for oid in small[:4]:
                spot = self.into(oid, cid)
                if spot:
                    yield Edit(oid, spot, "move a small object into the empty container")

    def rule_occlusion(self) -> Iterator[Edit]:
        occ, x = self.params["occluder"], self.original.evidence_object_ids[1]
        go = self.sg.geom[occ]
        away = OCCLUSION_AWAY_M
        if self.movable(x):
            for spot in self.table_spots(x, limit=4,
                                         keep=lambda p: np.linalg.norm(p - go.xy) >= away):
                yield Edit(x, spot, "move the hidden object away from the occluder")
        # fallback: hide another object behind the occluder instead
        from vtf.programs.view import image_point

        po = image_point(self.cam, go.centre)
        d_occ = planar_depth(self.cam, go.centre)
        from vtf.programs.view import image_footprint

        r_occ = diameter(image_footprint(self.cam, go)) / 2
        others = [o.id for o in self.sg.objects
                  if o.id not in (occ, x) and self.movable(o.id) and self.sg.name(o.id)]
        for oid in self.shuffled(others)[:3]:
            g = self.sg.geom[oid]
            z = self.rest_z_on_table(g)

            def behind(p, z=z):
                return planar_depth(self.cam, (p[0], p[1], z)) > d_occ + 0.03

            def order(p, z=z):  # image point near the occluder's outline: partly hidden
                d = float(np.linalg.norm(image_point(self.cam, (p[0], p[1], z)) - po))
                return abs(d - r_occ)

            for spot in self.table_spots(oid, keep=behind, order=order, limit=2):
                yield Edit(oid, spot, "move another object behind the occluder")
        # fallback: move the occluder in front of another object, partly covering it
        if not self.movable(occ):
            return
        z_occ = self.rest_z_on_table(go)
        targets = [(image_point(self.cam, self.sg.geom[o.id].centre),
                    planar_depth(self.cam, self.sg.geom[o.id].centre))
                   for o in self.sg.objects if o.id not in (occ, x) and self.sg.name(o.id)]

        def covers(p):
            q = image_point(self.cam, (p[0], p[1], z_occ))
            dq = planar_depth(self.cam, (p[0], p[1], z_occ))
            return min((abs(float(np.linalg.norm(q - t)) - r_occ) for t, d in targets
                        if d > dq + 0.03), default=math.inf)

        if targets:
            for spot in self.table_spots(occ, keep=lambda p: covers(p) < 0.5 * r_occ, order=covers,
                                         limit=4):
                yield Edit(occ, spot, "move the occluder in front of another object")

    def rule_reachability(self) -> Iterator[Edit]:
        zone = next(z for z in self.scene.zones if z.id == self.params["zone"])
        members = [o.id for o in self.sg.objects
                   if zone_signed_distance(zone, self.sg.geom[o.id].xy) >= 0]
        if members:
            for oid in members:
                if self.movable(oid):
                    for spot in self.table_spots(oid, limit=3, keep=lambda p: -0.25 <=
                                                 zone_signed_distance(zone, p) <= -0.035):
                        yield Edit(oid, spot, "move one member out of the zone")
        else:
            others = [o.id for o in self.sg.objects if self.movable(o.id) and self.sg.name(o.id)]
            for oid in others[:4]:
                for spot in self.table_spots(oid, limit=2,
                                             keep=lambda p: zone_signed_distance(zone, p) >= 0.035):
                    yield Edit(oid, spot, "move one non-member into the zone")

    def rule_free_space(self) -> Iterator[Edit]:
        item, a, b = self.params["item"], self.params["a"], self.params["b"]
        if not self.movable(b):
            return
        gi, ga, gb = self.sg.geom[item], self.sg.geom[a], self.sg.geom[b]
        w_max, w_min = diameter(gi.hull), min_width(gi.hull)
        local = gb.hull - gb.xy

        def gap(p):
            return polygon_distance(ga.hull, local + p)

        if self.original.answer:
            keep = lambda p: CLEARANCE_M <= gap(p) <= w_min - 0.025  # noqa: E731
        else:
            keep = lambda p: w_max + 0.03 <= gap(p) <= 0.30  # noqa: E731
        for spot in self.table_spots(b, keep=keep, order=lambda p: np.linalg.norm(p - gb.xy)):
            yield Edit(b, spot, "move b so the gap clearly changes side")

    def rule_gripper_view(self) -> Iterator[Edit]:
        x = self.original.evidence_object_ids[0]
        tip = np.asarray(self.scene.gripper.tip.position)
        if self.movable(x):
            for spot in self.table_spots(x, limit=3, keep=lambda p: np.linalg.norm(
                    p - tip[:2]) >= GRIPPER_AWAY_M):
                yield Edit(x, spot, "move the centre object out from under the gripper")
        # fallback: put a small object on top of (or into) the centre object
        gx = self.sg.geom[x]
        small = sorted((o.id for o in self.sg.objects if o.id != x and self.movable(o.id)
                        and _stackable(self.sg.geom[o.id])
                        and diameter(self.sg.geom[o.id].hull) <= min_width(gx.hull)),
                       key=lambda i: diameter(self.sg.geom[i].hull))  # fmt: skip
        for oid in small[:3]:
            spot = self.onto(oid, x)
            if spot:
                yield Edit(oid, spot, "place another object on the centre object")


def _flat_top(g: ObjGeom) -> bool:
    if g.entry is not None:
        return bool(g.entry.can_support or g.entry.container)
    return g.obj.shape in ("box", "cylinder")


def _stackable(g: ObjGeom) -> bool:
    if g.entry is not None:
        return bool(g.entry.stackable or g.entry.can_go_in_container)
    return g.obj.shape in ("box", "cylinder")


def make_twin(name: str, params: dict, scene: SceneSpec, camera_id: str,
              seed: int) -> TwinEdit | None:  # fmt: skip
    """The family's twin rule for one question, or None when no valid twin edit exists."""
    try:
        original = run_program(name, params, scene, camera_id)
    except ProgramReject:
        return None
    twin = _Twin(name, dict(params), scene, camera_id, original, make_rng(seed))
    checks = 0
    seen: set = set()
    for edit in twin.candidates():
        key = (edit.object_id, edit.position)
        if key in seen:
            continue
        seen.add(key)
        checks += 1
        result = twin.check(edit)
        if result is not None:
            return result
        if checks >= MAX_CHECKS:
            break
    return None
