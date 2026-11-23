"""Sampler v1: recipe + asset manifest + seed -> SceneSpec (as sampled, before the settle).

Adds to v0: objects from the asset manifest (or stage-2 primitives), clutter levels, two-high
stacks, objects inside passive containers, reach zones, look-alike pairs (same colour different
category; one asset at two sizes), identical duplicates, the low gripper (decision 1, brief 05),
three cameras (front, overhead, gripper) with camera-quality levels, a key light with a lighting
condition, an optional cm-grid table, and the recipe's question mix.

`sample_scene_v1` is a pure function of (recipe, asset manifest, cached asset meshes, scene_id,
seed). All randomness comes from one `make_rng(seed)` stream, drawn in this fixed order within
one layout attempt:

1. front and overhead cameras;
2. zone count and zone colours (no object may carry a zone colour);
3. object plan: clutter level and count, then feature groups (containers with contents, stacks,
   size pairs, colour pairs, a duplicate group) in a drawn order, then fill objects;
4. table placement, largest footprint first, each followed by its stacked or contained objects;
5. the gripper (fingertips 3 to 12 cm above the tallest resting object within 15 cm), then the
   gripper camera, mounted beside the palm looking along the tip's -Z;
6. zone shapes and positions; 7. grid, lighting, camera-quality levels; 8. object id order.

A failed step restarts the attempt on the same stream; after `max_layout_attempts` it raises
LayoutError. Every float is rounded to 6 digits before it is checked or stored.

Naming: apart from a planned duplicate group and size pairs, no two objects share a category with
the same colour word (or with a missing colour word), so every other object has a unique name.

Assets modelled standing on end (manifest notes say "standing on end") are laid on their side:
turned 90 degrees about the local axis that leaves the smaller of their x, y extents vertical.
"""

import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np

from vtf.core.assets import (
    REPO_ROOT,
    AssetEntry,
    AssetManifest,
    ContainerInfo,
    asset_cache_dir,
    asset_local_mesh,
    load_asset_manifest,
)
from vtf.core.geometry import (
    Pose,
    Quat,
    Vec3,
    look_at_camera_quat,
    quat_from_axis_angle,
    quat_multiply,
    round_vec,
    transform_points,
)
from vtf.core.rng import make_rng
from vtf.core.scene import (
    CameraQuality,
    CameraSpec,
    Gripper,
    KeyLight,
    Lighting,
    Placement,
    SceneMeta,
    SceneObject,
    SceneSpec,
    Table,
    Zone,
    build_gripper,
)
from vtf.core.vocab import linear_rgb
from vtf.scene.recipe import CamerasRecipe, RecipeV1
from vtf.scene.sampler import LayoutError, box_corners, gripper_view_points, in_view

GENERATOR_VERSION = "vtf.scene.sampler_v1 0.1.0"
Z_AXIS = (0.0, 0.0, 1.0)
ROUND_PRIMITIVES = ("cylinder", "sphere", "cone")
PRIMITIVE_SUPPORT = ("box", "cylinder")
PRIMITIVE_STACKABLE = ("box", "cylinder", "cone")
STANDING_ON_END = "standing on end"  # manifest note marking tools to lay on their side
TILTS: dict[str, Quat] = {
    "": (1.0, 0.0, 0.0, 0.0),
    "x": round_vec(quat_from_axis_angle((1.0, 0.0, 0.0), math.pi / 2)),  # type: ignore[dict-item]
    "y": round_vec(quat_from_axis_angle((0.0, 1.0, 0.0), math.pi / 2)),  # type: ignore[dict-item]
}


def _r(value: float) -> float:
    return round(float(value), 6) + 0.0


def _uniform(rng: np.random.Generator, bounds: tuple[float, float]) -> float:
    return _r(rng.uniform(bounds[0], bounds[1]))


def _yaw_quat(yaw_deg: float) -> Quat:
    return round_vec(quat_from_axis_angle(Z_AXIS, math.radians(yaw_deg)))  # type: ignore[return-value]


def _weighted(rng: np.random.Generator, weights: dict[str, float]) -> str:
    keys = [k for k, w in weights.items() if w > 0]
    p = np.array([weights[k] for k in keys], dtype=float)
    return keys[int(rng.choice(len(keys), p=p / p.sum()))]


def tilted_extents(dims: Vec3, tilt: str) -> Vec3:
    """Extents along the yaw frame's x, y and world z after the tilt (before yaw)."""
    dx, dy, dz = dims
    if tilt == "x":  # +90 about local x: local y -> world z, local z -> world -y
        return (dx, dz, dy)
    if tilt == "y":  # +90 about local y: local x -> world -z, local z -> world x
        return (dz, dy, dx)
    return (dx, dy, dz)


# Footprint geometry on the table plane --------------------------------------------------------

Rect = tuple[float, float, float, float, float]  # cx, cy, half_x, half_y, yaw_rad


def _axes(yaw: float) -> tuple[tuple[float, float], tuple[float, float]]:
    c, s = math.cos(yaw), math.sin(yaw)
    return (c, s), (-s, c)


def rects_overlap(a: Rect, b: Rect, gap: float = 0.0) -> bool:
    """Separating-axis test for two oriented rectangles, closer than `gap` counts as overlap."""
    axes_a, axes_b = _axes(a[4]), _axes(b[4])
    dx, dy = b[0] - a[0], b[1] - a[1]
    for ax, ay in (*axes_a, *axes_b):
        ra = a[2] * abs(axes_a[0][0] * ax + axes_a[0][1] * ay) + a[3] * abs(
            axes_a[1][0] * ax + axes_a[1][1] * ay
        )
        rb = b[2] * abs(axes_b[0][0] * ax + axes_b[0][1] * ay) + b[3] * abs(
            axes_b[1][0] * ax + axes_b[1][1] * ay
        )
        if abs(dx * ax + dy * ay) > ra + rb + gap:
            return False
    return True


def point_rect_distance(x: float, y: float, rect: Rect) -> float:
    """Distance on the table plane from a point to an oriented rectangle (0 inside)."""
    (ux, uy), (vx, vy) = _axes(rect[4])
    px, py = x - rect[0], y - rect[1]
    lx, ly = px * ux + py * uy, px * vx + py * vy
    return math.hypot(max(abs(lx) - rect[2], 0.0), max(abs(ly) - rect[3], 0.0))


def zone_signed_distance(zone: Zone, x: float, y: float) -> float:
    """Signed distance from (x, y) to the zone boundary: negative inside, positive outside."""
    px, py = x - zone.center_xy[0], y - zone.center_xy[1]
    if zone.shape == "circle":
        return math.hypot(px, py) - float(zone.radius or 0.0)
    (ux, uy), (vx, vy) = _axes(math.radians(zone.yaw_deg))
    lx, ly = abs(px * ux + py * uy), abs(px * vx + py * vy)
    hx, hy = (s / 2 for s in zone.size_xy or (0.0, 0.0))
    if lx <= hx and ly <= hy:
        return -min(hx - lx, hy - ly)
    return math.hypot(max(lx - hx, 0.0), max(ly - hy, 0.0))


def zone_contains(zone: Zone, x: float, y: float) -> bool:
    """Reachability rule (spec/families.md): the object's centre lies inside the zone."""
    return zone_signed_distance(zone, x, y) <= 0.0


def zone_outline_points(zone: Zone, n: int = 32) -> np.ndarray:
    """(n, 2) points on the zone boundary (for view checks and previews)."""
    cx, cy = zone.center_xy
    if zone.shape == "circle":
        a = np.linspace(0, 2 * np.pi, n, endpoint=False)
        r = float(zone.radius or 0.0)
        return np.stack([cx + r * np.cos(a), cy + r * np.sin(a)], axis=1)
    hx, hy = (s / 2 for s in zone.size_xy or (0.0, 0.0))
    t = np.linspace(-1, 1, max(n // 4, 2), endpoint=False)
    zero = np.zeros_like(t)
    local = np.concatenate(
        [
            np.stack([t * hx, zero - hy], 1),
            np.stack([zero + hx, t * hy], 1),
            np.stack([-t * hx, zero + hy], 1),
            np.stack([zero - hx, -t * hy], 1),
        ]
    )
    (ux, uy), (vx, vy) = _axes(math.radians(zone.yaw_deg))
    return np.stack(
        [cx + local[:, 0] * ux + local[:, 1] * vx, cy + local[:, 0] * uy + local[:, 1] * vy],
        axis=1,
    )


# Candidates and plan items --------------------------------------------------------------------


@dataclass(frozen=True, eq=False)  # hashed by identity: entries are pydantic models
class Candidate:
    """Something the sampler can place: a manifest entry or a stage-2 primitive shape."""

    pool: str  # assets or primitives
    source: str  # polyhaven, procedural or primitive
    shape: str  # asset id, or primitive shape
    category: str
    fixed_colour: str | None  # a textured asset's colour word
    colourable: bool  # primitives and uncoloured procedural assets take a recipe colour
    round: bool  # round footprint and no yaw (cylinders, spheres, cones)
    can_support: bool
    stackable: bool
    can_go_in: bool
    container: ContainerInfo | None
    passive: bool
    tilt: str = ""  # "", "x" or "y": laid on its side about that local axis
    entry: AssetEntry | None = None

    @property
    def prim_shape(self) -> str | None:
        """The primitive shape for primitives and procedural assets (analytic geometry)."""
        if self.source == "primitive":
            return self.shape
        if self.source == "procedural" and self.entry is not None:
            return self.entry.shape
        return None


@dataclass
class Item:
    cand: Candidate
    colour: str | None
    dims: Vec3  # local extents (SceneObject.dimensions)
    scale: float = 1.0
    size_word: str | None = None
    group: str = "fill"
    kind: str = "table"  # table, on, in
    parent: int | None = None  # plan index of the support or container
    pose: Pose | None = None
    yaw_deg: float = 0.0
    children: list[int] = field(default_factory=list)

    @property
    def foot(self) -> Vec3:
        """Extents in the yaw frame (x, y) and the vertical height, after the tilt."""
        return tilted_extents(self.dims, self.cand.tilt)

    @property
    def half_xy(self) -> tuple[float, float]:
        return self.foot[0] / 2, self.foot[1] / 2

    @property
    def height(self) -> float:
        return self.foot[2]

    @property
    def area(self) -> float:
        return self.foot[0] * self.foot[1]

    def rect(self) -> Rect:
        assert self.pose is not None
        return (*self.pose.position[:2], *self.half_xy, math.radians(self.yaw_deg))

    def spawn_top(self) -> float:
        assert self.pose is not None
        return self.pose.position[2] + self.height / 2

    def rotation(self, yaw_deg: float) -> Quat:
        return round_vec(quat_multiply(_yaw_quat(yaw_deg), TILTS[self.cand.tilt]))  # type: ignore[return-value]


def _tilt_for(entry: AssetEntry) -> str:
    if STANDING_ON_END not in entry.notes:
        return ""
    return "x" if entry.size_m[1] <= entry.size_m[0] else "y"


def _candidates(recipe: RecipeV1, manifest: AssetManifest) -> list[Candidate]:
    ob = recipe.objects
    out: list[Candidate] = []
    if ob.pool_weights.get("assets", 0.0) > 0:
        pool = ob.assets
        for e in manifest.assets:
            if pool.ids is not None and e.id not in pool.ids:
                continue
            if pool.categories is not None and e.category not in pool.categories:
                continue
            if e.id in pool.exclude_ids or pool.source_weights.get(e.source, 0.0) <= 0:
                continue
            colourable = e.source == "procedural" and e.color_name is None
            tilt = _tilt_for(e)
            out.append(
                Candidate(
                    pool="assets",
                    source=e.source,
                    shape=e.id,
                    category=e.category,
                    fixed_colour=None if colourable else e.color_name,
                    colourable=colourable,
                    round=e.source == "procedural" and e.shape in ROUND_PRIMITIVES,
                    can_support=e.can_support and e.container is None and not tilt,
                    stackable=e.stackable,
                    can_go_in=e.can_go_in_container,
                    container=e.container,
                    passive=e.passive,
                    tilt=tilt,
                    entry=e,
                )
            )
    if ob.pool_weights.get("primitives", 0.0) > 0:
        for shape in ob.primitives.shapes:
            out.append(
                Candidate(
                    pool="primitives",
                    source="primitive",
                    shape=shape,
                    category=shape,
                    fixed_colour=None,
                    colourable=True,
                    round=shape in ROUND_PRIMITIVES,
                    can_support=shape in PRIMITIVE_SUPPORT,
                    stackable=shape in PRIMITIVE_STACKABLE,
                    can_go_in=True,
                    container=None,
                    passive=False,
                )
            )
    if not out:
        raise ValueError(f"recipe {recipe.name}: no objects allowed by the pool filters")
    return out


def _candidate_weights(recipe: RecipeV1, cands: list[Candidate]) -> dict[Candidate, float]:
    """Each (pool, source) class gets pool weight x source weight, split evenly among members."""
    ob = recipe.objects
    sizes: dict[tuple[str, str], int] = {}
    for c in cands:
        sizes[(c.pool, c.source)] = sizes.get((c.pool, c.source), 0) + 1
    weights = {}
    for c in cands:
        src = 1.0 if c.pool == "primitives" else ob.assets.source_weights.get(c.source, 0.0)  # type: ignore[call-overload]
        weights[c] = ob.pool_weights.get(c.pool, 0.0) * src / sizes[(c.pool, c.source)]  # type: ignore[call-overload]
    return weights


@lru_cache(maxsize=256)
def _mesh_footprint_radius(cache_dir: str, entry_json: str, scale: float, tilt: str) -> float:
    entry = AssetEntry.model_validate_json(entry_json)
    verts = asset_local_mesh(entry, scale).vertices
    if tilt == "x":
        verts = verts[:, [0, 2, 1]]
    elif tilt == "y":
        verts = verts[:, [2, 1, 0]]
    return float(np.hypot(verts[:, 0], verts[:, 1]).max())


def footprint_radius(item: Item) -> float:
    """Radius of the circle about the centre that holds the item's footprint at any yaw."""
    shape = item.cand.prim_shape
    if shape is not None:
        return item.dims[0] / 2 if shape in ROUND_PRIMITIVES else math.hypot(*item.dims[:2]) / 2
    entry = item.cand.entry
    assert entry is not None
    path = asset_cache_dir() / (entry.file or "")
    if not path.is_file():
        raise FileNotFoundError(
            f"asset {entry.id}: {path} is not cached; fetch it with "
            f"`uv run python assets/fetch_polyhaven.py {entry.id} --kind model`"
        )
    radius = _mesh_footprint_radius(
        str(asset_cache_dir()), entry.model_dump_json(), item.scale, item.cand.tilt
    )
    return _r(radius)


# Planning: what to place ----------------------------------------------------------------------


class _Planner:
    """Chooses what to place: candidates, colours, sizes and groups (no positions)."""

    def __init__(
        self, rng: np.random.Generator, recipe: RecipeV1, cands: list[Candidate], banned: set[str]
    ) -> None:
        self.rng, self.recipe = rng, recipe
        self.colours = [c for c in recipe.objects.colours if c not in banned]
        self.cands = [c for c in cands if c.fixed_colour not in banned]
        self.weights = _candidate_weights(recipe, self.cands)
        self.items: list[Item] = []
        self.names: list[tuple[str, str | None]] = []  # (category, colour) in use
        table_area = recipe.table.size_xy[0] * recipe.table.size_xy[1]
        self.budget = recipe.objects.fill_ratio * table_area

    # naming --------------------------------------------------------------------------------
    def name_free(self, category: str, colour: str | None) -> bool:
        return not any(
            cat == category and (col == colour or col is None or colour is None)
            for cat, col in self.names
        )

    def colours_for(self, cand: Candidate) -> list[str | None]:
        """Colours this candidate may take now with a unique name."""
        if cand.colourable:
            return [c for c in self.colours if self.name_free(cand.category, c)]
        return [cand.fixed_colour] if self.name_free(cand.category, cand.fixed_colour) else []

    def can_take(self, cand: Candidate, colour: str) -> bool:
        return (cand.colourable and colour in self.colours) or cand.fixed_colour == colour

    def choose_colour(self, cand: Candidate) -> str | None:
        options = self.colours_for(cand)
        return options[int(self.rng.integers(len(options)))]

    # drawing ---------------------------------------------------------------------------------
    def min_area(self, cand: Candidate) -> float:
        if cand.source == "primitive":
            return self.recipe.objects.primitives.size_m[0] ** 2
        assert cand.entry is not None
        fx, fy, _ = tilted_extents(cand.entry.size_m, cand.tilt)
        return fx * fy

    def pick(self, ok, need_name: bool = True, on_table: bool = True) -> Candidate | None:
        """Weighted draw among candidates passing `ok`, with a free name and (for table-level
        objects) a footprint that fits the remaining area budget."""
        pool = [
            c for c in self.cands
            if ok(c)
            and (not need_name or self.colours_for(c))
            and (not on_table or self.min_area(c) <= self.budget)
        ]  # fmt: skip
        if not pool:
            return None
        p = np.array([self.weights[c] for c in pool])
        if p.sum() <= 0:
            return None
        return pool[int(self.rng.choice(len(pool), p=p / p.sum()))]

    def dims(self, cand: Candidate, scale: float = 1.0) -> Vec3:
        """Local extents: random for primitives, the manifest size times `scale` for assets."""
        if cand.source == "primitive":
            size = self.recipe.objects.primitives.size_m
            if cand.shape == "box":
                base = (_uniform(self.rng, size), _uniform(self.rng, size),
                        _uniform(self.rng, size))  # fmt: skip
            elif cand.shape == "sphere":
                d = _uniform(self.rng, size)
                base = (d, d, d)
            else:
                d = _uniform(self.rng, size)
                base = (d, d, _uniform(self.rng, size))
        else:
            assert cand.entry is not None
            base = cand.entry.size_m
        return tuple(_r(v * scale) for v in base)  # type: ignore[return-value]

    def area(self, cand: Candidate, dims: Vec3) -> float:
        fx, fy, _ = tilted_extents(dims, cand.tilt)
        return fx * fy

    def add(self, cand: Candidate, colour: str | None, dims: Vec3, **kw) -> int:
        item = Item(cand=cand, colour=colour, dims=dims, **kw)
        self.items.append(item)
        if item.kind == "table":
            self.budget -= item.area
        return len(self.items) - 1

    def claim(self, cand: Candidate) -> str | None:
        colour = self.choose_colour(cand)
        self.names.append((cand.category, colour))
        return colour

    def single(self, ok, group: str) -> int | None:
        """One table-level object (a few draws, since primitive sizes are random)."""
        for _ in range(4):
            cand = self.pick(ok)
            if cand is None:
                return None
            dims = self.dims(cand)
            if self.area(cand, dims) > self.budget:
                continue
            return self.add(cand, self.claim(cand), dims, group=group)
        return None

    # groups ------------------------------------------------------------------------------------
    def container_group(self, room: int) -> int:
        if room < 1:
            return 0
        ci = self.single(lambda c: c.container is not None, "container")
        if ci is None:
            return 0
        box = self.items[ci]
        info = box.cand.container
        assert info is not None
        cr = self.recipe.containers
        if info.shape == "circle":
            inner_r = float(info.inner_radius_m or 0.0)
            interior_area = math.pi * inner_r**2
        else:
            ix, iy = info.inner_size_xy or (0.0, 0.0)
            inner_r = min(ix, iy) / 2
            interior_area = ix * iy
        depth = info.rim_z_m - info.floor_z_m
        want = min(int(self.rng.integers(cr.items[0], cr.items[1] + 1)), info.capacity, room - 1)

        def fits(probe: Item) -> bool:
            """Fits the interior and is squat or short enough not to topple out."""
            squat = probe.height <= 1.2 * min(probe.foot[0], probe.foot[1])
            return footprint_radius(probe) <= inner_r - cr.wall_margin_m and (
                squat or probe.height <= cr.max_height_ratio * depth
            )

        def ok(c: Candidate) -> bool:
            if not c.can_go_in or c.container is not None:
                return False
            # Asset sizes are fixed (no draw): filter by fit before drawing.
            return c.source == "primitive" or fits(Item(cand=c, colour=None, dims=self.dims(c)))

        used_area = 0.0
        tries = 0
        while len(box.children) < want and tries < 4 * want:
            tries += 1
            cand = self.pick(ok, on_table=False)
            if cand is None:
                break
            dims = self.dims(cand)
            probe = Item(cand=cand, colour=None, dims=dims)
            area = math.pi * footprint_radius(probe) ** 2
            if (
                not fits(probe)
                or used_area + area > cr.max_fill * interior_area
            ):
                continue
            used_area += area
            child = self.add(cand, self.claim(cand), dims, group="content", kind="in", parent=ci)
            box.children.append(child)
        return 1 + len(box.children)

    def stack_group(self, room: int) -> int:
        if room < 2:
            return 0
        si = self.single(lambda c: c.can_support, "support")
        if si is None:
            return 0
        base = self.items[si]
        limit = self.recipe.stacks.max_size_ratio * min(base.foot[0], base.foot[1])
        for _ in range(8):
            cand = self.pick(lambda c: c.stackable and c.container is None, on_table=False)
            if cand is None:
                break
            dims = self.dims(cand)
            fx, fy, _ = tilted_extents(dims, cand.tilt)
            if max(fx, fy) > limit:
                continue
            child = self.add(cand, self.claim(cand), dims, group="stacked", kind="on", parent=si)
            base.children.append(child)
            return 2
        return 1

    def size_pair(self, room: int) -> int:
        if room < 2:
            return 0
        cand = self.pick(lambda c: c.container is None)
        if cand is None:
            return 0
        lk = self.recipe.lookalikes
        if self.rng.random() < 0.5:
            other, words = round(_uniform(self.rng, lk.small_scale), 4), ("large", "small")
        else:
            other, words = round(_uniform(self.rng, lk.large_scale), 4), ("small", "large")
        if cand.source == "primitive":  # primitives carry their size in dimensions
            base = self.dims(cand)
            dims = [base, tuple(_r(v * other) for v in base)]
            scales = [1.0, 1.0]
        else:
            dims = [self.dims(cand), self.dims(cand, other)]
            scales = [1.0, other]
        if sum(self.area(cand, d) for d in dims) > self.budget:  # type: ignore[arg-type]
            return 0
        colour = self.claim(cand)
        for d, s, w in zip(dims, scales, words, strict=True):
            self.add(cand, colour, d, scale=s, size_word=w, group="size_pair")  # type: ignore[arg-type]
        return 2

    def colour_pair(self, room: int) -> int:
        if room < 2:
            return 0
        for _ in range(6):
            first = self.pick(lambda c: c.container is None)
            if first is None:
                return 0
            options = [c for c in self.colours_for(first) if c is not None]
            for oi in self.rng.permutation(len(options)):
                colour = options[int(oi)]

                def ok(c: Candidate, colour: str = colour, first: Candidate = first) -> bool:
                    return (
                        c.category != first.category
                        and c.container is None
                        and self.can_take(c, colour)
                        and self.name_free(c.category, colour)
                    )

                second = self.pick(ok, need_name=False)
                if second is None:
                    continue
                d1, d2 = self.dims(first), self.dims(second)
                if self.area(first, d1) + self.area(second, d2) > self.budget:
                    return 0
                for cand, d in ((first, d1), (second, d2)):
                    self.names.append((cand.category, colour))
                    self.add(cand, colour, d, group="colour_pair")
                return 2
        return 0

    def duplicate_group(self, room: int) -> int:
        dr = self.recipe.duplicates
        copies = min(int(self.rng.integers(dr.copies[0], dr.copies[1] + 1)), room)
        if copies < 2:
            return 0
        cand = self.pick(lambda c: c.container is None)
        if cand is None:
            return 0
        dims = self.dims(cand)
        if copies * self.area(cand, dims) > self.budget:
            return 0
        colour = self.claim(cand)
        for _ in range(copies):
            self.add(cand, colour, dims, group="duplicate")
        return copies


def _plan(
    rng: np.random.Generator, recipe: RecipeV1, cands: list[Candidate], banned: set[str]
) -> tuple[str, int, list[Item]]:
    ob = recipe.objects
    level = _weighted(rng, {k: v.weight for k, v in ob.clutter.items()})
    lo, hi = ob.clutter[level].count
    target = int(rng.integers(lo, hi + 1))
    planner = _Planner(rng, recipe, cands, banned)
    lk, cr, sr = recipe.lookalikes, recipe.containers, recipe.stacks
    groups: list[str] = []
    groups += ["container"] * int(rng.integers(cr.count[0], cr.count[1] + 1))
    groups += ["stack"] * int(rng.integers(sr.count[0], sr.count[1] + 1))
    groups += ["size_pair"] * int(rng.integers(lk.size_pairs[0], lk.size_pairs[1] + 1))
    groups += ["colour_pair"] * int(rng.integers(lk.colour_pairs[0], lk.colour_pairs[1] + 1))
    if rng.random() < recipe.duplicates.probability:
        groups.append("duplicate")
    groups = [groups[int(i)] for i in rng.permutation(len(groups))]
    run = {
        "container": planner.container_group,
        "stack": planner.stack_group,
        "size_pair": planner.size_pair,
        "colour_pair": planner.colour_pair,
        "duplicate": planner.duplicate_group,
    }
    for g in groups:
        run[g](target - len(planner.items))
    while len(planner.items) < target:
        # Fill objects never open a new container or stack: those come only from the groups.
        if planner.single(lambda c: c.container is None, "fill") is None:
            break  # no candidate with a unique name fits the footprint budget
    return level, target, planner.items


# Cameras --------------------------------------------------------------------------------------


def _sample_front(rng: np.random.Generator, recipe: RecipeV1) -> CameraSpec:
    cr, c = recipe.cameras, recipe.cameras.front
    jitter = c.target_jitter_m
    target = (_r(rng.uniform(-jitter, jitter)), _r(rng.uniform(-jitter, jitter)),
              recipe.table.height)  # fmt: skip
    distance = _uniform(rng, c.distance_m)
    elevation = math.radians(_uniform(rng, c.elevation_deg))
    azimuth = math.radians(_uniform(rng, c.azimuth_deg))
    horizontal = distance * math.cos(elevation)
    eye = round_vec(
        (
            target[0] + horizontal * math.sin(azimuth),
            target[1] - horizontal * math.cos(azimuth),
            target[2] + distance * math.sin(elevation),
        )
    )
    return CameraSpec(
        id="front",
        rig="front",
        pose=Pose(position=eye, rotation=round_vec(look_at_camera_quat(eye, target))),
        focal_mm=_uniform(rng, c.focal_mm),
        sensor_width_mm=cr.sensor_width_mm,
        resolution=cr.resolution,
        clip_start=cr.clip_start,
        clip_end=cr.clip_end,
    )


def _sample_overhead(rng: np.random.Generator, recipe: RecipeV1) -> CameraSpec:
    """Straight down with small jitter; image up is world +y (the table's far side)."""
    cr, c = recipe.cameras, recipe.cameras.overhead
    j = c.xy_jitter_m
    eye = (_r(rng.uniform(-j, j)), _r(rng.uniform(-j, j)),
           _r(recipe.table.height + _uniform(rng, c.height_m)))  # fmt: skip
    tilt = math.radians(_uniform(rng, c.tilt_deg))
    tilt_dir = math.radians(_uniform(rng, (0.0, 360.0)))
    roll = math.radians(_uniform(rng, c.roll_deg))
    q_roll = quat_from_axis_angle(Z_AXIS, roll)
    q_tilt = quat_from_axis_angle((math.cos(tilt_dir), math.sin(tilt_dir), 0.0), tilt)
    return CameraSpec(
        id="overhead",
        rig="overhead",
        pose=Pose(position=eye, rotation=round_vec(quat_multiply(q_tilt, q_roll))),
        focal_mm=_uniform(rng, c.focal_mm),
        sensor_width_mm=cr.sensor_width_mm,
        resolution=cr.resolution,
        clip_start=cr.clip_start,
        clip_end=cr.clip_end,
    )


def gripper_camera(
    gripper: Gripper, hfov_deg: float, mount_gap_m: float, cams: CamerasRecipe
) -> CameraSpec:
    """Wrist camera beside the palm's +y face at palm mid-height, looking along the tip's -Z.

    The camera frame equals the tip frame: image right is the finger-opening axis (tip +x),
    image up is tip +y, and it looks down the approach direction.
    """
    offset = (0.0, gripper.palm_size[1] / 2 + mount_gap_m,
              gripper.finger_length + gripper.palm_size[2] / 2)  # fmt: skip
    centre = transform_points(gripper.tip.matrix(), [offset])[0]
    focal = cams.sensor_width_mm / 2 / math.tan(math.radians(hfov_deg) / 2)
    return CameraSpec(
        id="gripper",
        rig="gripper",
        pose=Pose(position=round_vec(centre), rotation=gripper.tip.rotation),
        focal_mm=_r(focal),
        sensor_width_mm=cams.sensor_width_mm,
        resolution=cams.resolution,
        clip_start=cams.clip_start,
        clip_end=cams.clip_end,
    )


# Placement: where things go -------------------------------------------------------------------


def _rest_top(items: list[Item], i: int, table_top: float) -> float:
    """Expected resting top after the settle (spawn gaps closed)."""
    it = items[i]
    if it.kind == "table":
        return table_top + it.height
    assert it.parent is not None
    if it.kind == "on":
        return _rest_top(items, it.parent, table_top) + it.height
    parent = items[it.parent]
    info = parent.cand.container
    assert info is not None
    return table_top + info.floor_z_m * parent.scale + it.height


class _Placer:
    def __init__(self, rng: np.random.Generator, recipe: RecipeV1, views: list[CameraSpec]):
        self.rng, self.recipe, self.views = rng, recipe, views
        self.rects: list[tuple[int, Rect]] = []
        self.top = recipe.table.height
        self.margin = recipe.cameras.view_margin_px

    def visible(self, pose: Pose, dims: Vec3) -> bool:
        corners = box_corners(pose, dims)
        return all(in_view(cam, corners, self.margin) for cam in self.views)

    def free(self, rect: Rect, skip: int | None = None) -> bool:
        gap = self.recipe.placement.gap_m
        return not any(rects_overlap(rect, r, gap) for j, r in self.rects if j != skip)

    def draw_yaw(self, item: Item) -> float:
        return 0.0 if item.cand.round else _uniform(self.rng, self.recipe.objects.yaw_deg)

    def place_table(self, items: list[Item], i: int) -> bool:
        it, pl = items[i], self.recipe.placement
        half_x, half_y = (s / 2 for s in self.recipe.table.size_xy)
        drop = 0.0 if it.cand.passive else self.recipe.objects.drop_m  # passive: on the table
        z = _r(self.top + it.height / 2 + drop)
        for _ in range(pl.tries_per_object):
            yaw = self.draw_yaw(it)
            c, s = abs(math.cos(math.radians(yaw))), abs(math.sin(math.radians(yaw)))
            hx, hy = it.half_xy
            ax, ay = c * hx + s * hy, s * hx + c * hy
            lim_x, lim_y = half_x - pl.table_margin_m - ax, half_y - pl.table_margin_m - ay
            if lim_x < 0 or lim_y < 0:
                continue
            x, y = _r(self.rng.uniform(-lim_x, lim_x)), _r(self.rng.uniform(-lim_y, lim_y))
            rect = (x, y, hx, hy, math.radians(yaw))
            if not self.free(rect):
                continue
            pose = Pose(position=(x, y, z), rotation=it.rotation(yaw))
            if not self.visible(pose, it.dims):
                continue
            it.pose, it.yaw_deg = pose, yaw
            n_rects = len(self.rects)
            self.rects.append((i, rect))
            if all(self.place_child(items, i, k) for k in it.children):
                return True
            del self.rects[n_rects:]  # a child did not fit here: move the parent
            for k in [i, *it.children]:
                items[k].pose = None
        return False

    def place_child(self, items: list[Item], parent_i: int, i: int) -> bool:
        parent, it = items[parent_i], items[i]
        assert parent.pose is not None
        drop = self.recipe.objects.drop_m
        px, py, pz = parent.pose.position
        (ux, uy), (vx, vy) = _axes(math.radians(parent.yaw_deg))
        siblings = [items[k] for k in parent.children if k != i and items[k].pose is not None]
        radius = footprint_radius(it) if it.kind == "in" else 0.0
        for _ in range(max(self.recipe.placement.tries_per_object // 4, 10)):
            if it.kind == "on":
                sr = self.recipe.stacks
                jitter = sr.yaw_jitter_deg
                yaw = (
                    0.0 if it.cand.round else _r(parent.yaw_deg + self.rng.uniform(-jitter, jitter))
                )
                f = sr.max_offset_frac
                ox = self.rng.uniform(-f, f) * parent.half_xy[0]
                oy = self.rng.uniform(-f, f) * parent.half_xy[1]
                z = _r(parent.spawn_top() + it.height / 2 + drop)
            else:
                info = parent.cand.container
                assert info is not None
                yaw = self.draw_yaw(it)
                wall = self.recipe.containers.wall_margin_m
                if info.shape == "circle":
                    room = float(info.inner_radius_m or 0.0) * parent.scale - radius - wall
                    rho = max(room, 0.0) * math.sqrt(self.rng.random())
                    phi = self.rng.uniform(0, 2 * math.pi)
                    ox, oy = rho * math.cos(phi), rho * math.sin(phi)
                else:
                    ix, iy = info.inner_size_xy or (0.0, 0.0)
                    rx = max(ix * parent.scale / 2 - radius - wall, 0.0)
                    ry = max(iy * parent.scale / 2 - radius - wall, 0.0)
                    ox, oy = self.rng.uniform(-rx, rx), self.rng.uniform(-ry, ry)
                bottom = pz - parent.height / 2
                z = _r(bottom + info.floor_z_m * parent.scale + it.height / 2 + drop)
            x, y = _r(px + ox * ux + oy * vx), _r(py + ox * uy + oy * vy)
            if it.kind == "in" and any(
                math.dist((x, y), s.pose.position[:2])  # type: ignore[union-attr]
                < radius + footprint_radius(s) + 0.003
                for s in siblings
            ):
                continue
            rect = (x, y, *it.half_xy, math.radians(yaw))
            if it.kind == "on" and not self.free(rect, skip=parent_i):
                continue
            pose = Pose(position=(x, y, z), rotation=it.rotation(yaw))
            if not self.visible(pose, it.dims):
                continue
            it.pose, it.yaw_deg = pose, yaw
            if it.kind == "on":
                self.rects.append((i, rect))
            return True
        return False


def _place(
    rng: np.random.Generator, recipe: RecipeV1, items: list[Item], views: list[CameraSpec]
) -> bool:
    placer = _Placer(rng, recipe, views)
    table = [i for i, it in enumerate(items) if it.kind == "table"]
    table.sort(key=lambda i: (-items[i].area, i))
    return all(placer.place_table(items, i) for i in table)


def _sample_gripper(
    rng: np.random.Generator, recipe: RecipeV1, items: list[Item], views: list[CameraSpec]
) -> tuple[Gripper, float] | None:
    """The low gripper: fingertips `clearance_m` above the tallest resting object whose
    footprint is within `neighbourhood_m` of the tip (or the table top), touching nothing."""
    g = recipe.gripper
    top = recipe.table.height
    half_x, half_y = (s / 2 for s in recipe.table.size_xy)
    lim_x, lim_y = half_x - g.xy_margin_m, half_y - g.xy_margin_m
    rects = [it.rect() for it in items]
    rest = [_rest_top(items, i, top) for i in range(len(items))]
    margin = recipe.cameras.view_margin_px
    for _ in range(g.tries):
        if items and rng.random() < g.over_object_probability:
            anchor = items[int(rng.integers(len(items)))].pose
            assert anchor is not None
            rho = g.over_object_offset_m * math.sqrt(rng.random())
            phi = rng.uniform(0, 2 * math.pi)
            x = _r(anchor.position[0] + rho * math.cos(phi))
            y = _r(anchor.position[1] + rho * math.sin(phi))
        else:
            x, y = _r(rng.uniform(-lim_x, lim_x)), _r(rng.uniform(-lim_y, lim_y))
        yaw = _uniform(rng, g.yaw_deg)
        clearance = _uniform(rng, g.clearance_m)
        if abs(x) > lim_x or abs(y) > lim_y:
            continue
        near = [i for i, r in enumerate(rects) if point_rect_distance(x, y, r) <= g.neighbourhood_m]
        base = max([rest[i] for i in near], default=top)
        tip_z = _r(base + clearance)
        gripper = build_gripper(Pose(position=(x, y, tip_z), rotation=_yaw_quat(yaw)))
        # Never touching: every gripper part lies within `reach` of the tip axis in xy, so any
        # object whose footprint comes that close must spawn below the fingertips.
        reach = (
            max(
                math.hypot(gripper.palm_size[0], gripper.palm_size[1]) / 2,
                gripper.opening / 2 + gripper.finger_thickness,
                gripper.arm_radius,
            )
            + g.min_part_gap_m
        )
        if any(
            point_rect_distance(x, y, rects[i]) <= reach
            and items[i].spawn_top() > tip_z - g.min_part_gap_m
            for i in range(len(items))
        ):
            continue
        if not all(in_view(cam, gripper_view_points(gripper), margin) for cam in views):
            continue
        return gripper, _r(tip_z - base)
    return None


def _zone_bound(zone: Zone) -> float:
    if zone.shape == "circle":
        return float(zone.radius or 0.0)
    return math.hypot(*(zone.size_xy or (0.0, 0.0))) / 2


def _sample_zones(
    rng: np.random.Generator,
    recipe: RecipeV1,
    colours: list[str],
    items: list[Item],
    views: list[CameraSpec],
) -> list[Zone]:
    """Each zone has objects inside and outside; centres keep clear of the boundary when they
    can (first half of the tries). A zone that never fits is dropped."""
    zr = recipe.zones
    half_x, half_y = (s / 2 for s in recipe.table.size_xy)
    margin = recipe.placement.table_margin_m
    centres = [it.pose.position[:2] for it in items if it.pose is not None]
    zones: list[Zone] = []
    for colour in colours:
        for attempt in range(zr.tries):
            strict = attempt < zr.tries // 2
            if rng.random() < zr.rect_weight:
                w, h = _uniform(rng, zr.rect_size_m), _uniform(rng, zr.rect_size_m)
                yaw = _uniform(rng, zr.yaw_deg)
                c, s = abs(math.cos(math.radians(yaw))), abs(math.sin(math.radians(yaw)))
                ex, ey = (c * w + s * h) / 2, (s * w + c * h) / 2
                shape: dict = {"shape": "rect", "size_xy": (w, h), "yaw_deg": yaw}
                bound = math.hypot(w, h) / 2
            else:
                radius = _uniform(rng, zr.circle_radius_m)
                ex = ey = bound = radius
                shape = {"shape": "circle", "radius": radius}
            lim_x, lim_y = half_x - margin - ex, half_y - margin - ey
            if lim_x < 0 or lim_y < 0:
                continue
            cx, cy = _r(rng.uniform(-lim_x, lim_x)), _r(rng.uniform(-lim_y, lim_y))
            zone = Zone(
                id=f"zone_{len(zones)}",
                center_xy=(cx, cy),
                color_name=colour,
                rgb=linear_rgb(colour),
                line_width_m=zr.line_width_m,
                **shape,
            )
            d = [zone_signed_distance(zone, x, y) for x, y in centres]
            inside = sum(v <= 0 for v in d)
            if inside < zr.min_inside or len(d) - inside < zr.min_outside:
                continue
            if strict and min((abs(v) for v in d), default=1.0) < zr.boundary_margin_m:
                continue
            if any(math.dist((cx, cy), z.center_xy) < bound + _zone_bound(z) + 0.02
                   for z in zones):  # fmt: skip
                continue
            pts = zone_outline_points(zone)
            pts3 = np.column_stack([pts, np.full(len(pts), recipe.table.height)])
            if not all(in_view(cam, pts3, recipe.cameras.view_margin_px) for cam in views):
                continue
            zones.append(zone)
            break
    return zones


def _sample_lighting(rng: np.random.Generator, recipe: RecipeV1) -> Lighting:
    lr = recipe.lighting
    condition = _weighted(rng, {k: v.weight for k, v in lr.conditions.items()})
    cond = lr.conditions[condition]
    hdri = lr.hdri_ids[int(rng.integers(len(lr.hdri_ids)))]
    key = KeyLight(
        kind="sun",
        elevation_deg=_uniform(rng, cond.key_elevation_deg),
        azimuth_deg=_uniform(rng, lr.key_azimuth_deg),
        strength=_uniform(rng, cond.key_strength),
        angle_deg=_uniform(rng, cond.key_angle_deg),
        rgb=round_vec(cond.key_rgb[int(rng.integers(len(cond.key_rgb)))]),  # type: ignore[arg-type]
    )
    return Lighting(
        hdri_id=hdri,
        strength=_uniform(rng, cond.hdri_strength),
        rotation_deg=_uniform(rng, lr.rotation_deg),
        key_light=key,
        condition=condition,
    )


def _sample_quality(rng: np.random.Generator, recipe: RecipeV1, rig: str) -> CameraQuality:
    q = recipe.cameras.quality
    level = _weighted(rng, q.per_rig.get(rig, q.weights))
    seed = int(rng.integers(0, 2**31 - 1))
    return CameraQuality(level=level, seed=seed, **q.presets.get(level, {}))  # type: ignore[arg-type]


# Assembly -------------------------------------------------------------------------------------


def _scene_object(item: Item, obj_id: str, pass_index: int, parent_id: str | None,
                  recipe: RecipeV1) -> SceneObject:  # fmt: skip
    cand = item.cand
    prim = recipe.objects.primitives
    if cand.source == "primitive" or cand.colourable:
        rgb = linear_rgb(item.colour)  # type: ignore[arg-type]
        rough, metal = prim.roughness, prim.metallic
    else:
        assert cand.entry is not None
        rgb, rough, metal = cand.entry.rgb, 0.45, 0.0
    return SceneObject(
        id=obj_id,
        source="primitive" if cand.source == "primitive" else "asset",
        shape=cand.shape,
        category=cand.category,
        color_name=item.colour,
        size_word=item.size_word,  # type: ignore[arg-type]
        rgb=rgb,
        roughness=rough,
        metallic=metal,
        dimensions=item.dims,
        scale=item.scale,
        pose=item.pose,  # type: ignore[arg-type]
        pass_index=pass_index,
        placement=Placement(kind=item.kind, parent_id=parent_id),  # type: ignore[arg-type]
    )


def _difficulty(level: str, items: list[Item], zones: list[Zone], lighting: Lighting,
                grid: bool, cameras: list[CameraSpec], clearance: float) -> dict:  # fmt: skip
    groups = [it.group for it in items]
    n_size, n_colour = groups.count("size_pair") // 2, groups.count("colour_pair") // 2
    diff: dict[str, int | float | str | bool] = {
        "clutter": level,
        "n_objects": len(items),
        "n_stacks": sum(it.kind == "on" for it in items),
        "n_containers": sum(it.cand.container is not None for it in items),
        "n_contained": sum(it.kind == "in" for it in items),
        "n_lookalike_pairs": n_size + n_colour,
        "n_size_pairs": n_size,
        "n_colour_pairs": n_colour,
        "n_duplicates": groups.count("duplicate"),
        "n_zones": len(zones),
        "lighting": lighting.condition,
        "grid": grid,
        "gripper_clearance_cm": round(clearance * 100, 2),
    }
    for cam in cameras:
        diff[f"quality_{cam.id}"] = cam.quality.level
    return diff


def resolve_manifest(recipe: RecipeV1, manifest: AssetManifest | None = None) -> AssetManifest:
    if manifest is not None:
        return manifest
    if recipe.asset_manifest:
        path = Path(recipe.asset_manifest)
        return load_asset_manifest(path if path.is_absolute() else REPO_ROOT / path)
    return load_asset_manifest()


def sample_scene_v1(
    recipe: RecipeV1, scene_id: str, seed: int, manifest: AssetManifest | None = None
) -> SceneSpec:
    """Sample one scene. Same (recipe, manifest, scene_id, seed) -> the same SceneSpec."""
    manifest = resolve_manifest(recipe, manifest)
    cands = _candidates(recipe, manifest)
    rigs = recipe.cameras.rigs
    rng = make_rng(seed)
    zr = recipe.zones
    half_x, half_y = (s / 2 for s in recipe.table.size_xy)
    table_corners = np.array([(sx * half_x, sy * half_y, recipe.table.height)
                              for sx in (-1, 1) for sy in (-1, 1)])  # fmt: skip
    for _ in range(recipe.max_layout_attempts):
        cams: dict[str, CameraSpec] = {}
        if "front" in rigs:
            cams["front"] = _sample_front(rng, recipe)
        if "overhead" in rigs:
            cams["overhead"] = _sample_overhead(rng, recipe)
            if not in_view(cams["overhead"], table_corners, recipe.cameras.view_margin_px):
                continue  # the whole table must be in the overhead view
        views = list(cams.values())

        n_zones = int(rng.integers(zr.count[0], zr.count[1] + 1))
        zone_colours = [zr.colours[int(i)] for i in rng.permutation(len(zr.colours))[:n_zones]]

        level, _target, items = _plan(rng, recipe, cands, set(zone_colours))
        if not items or not _place(rng, recipe, items, views):
            continue
        sampled = _sample_gripper(rng, recipe, items, views)
        if sampled is None:
            continue
        gripper, clearance = sampled
        if "gripper" in rigs:
            gc = recipe.cameras.gripper
            cams["gripper"] = gripper_camera(
                gripper, _uniform(rng, gc.hfov_deg), gc.mount_gap_m, recipe.cameras
            )
        zones = _sample_zones(rng, recipe, zone_colours, items, views)

        grid = bool(rng.random() < recipe.table.grid_fraction)
        lighting = _sample_lighting(rng, recipe)
        cameras = [
            cams[rig].model_copy(update={"quality": _sample_quality(rng, recipe, rig)})
            for rig in ("front", "overhead", "gripper")
            if rig in cams
        ]
        order = [int(i) for i in rng.permutation(len(items))]
        ids = {plan_i: f"obj_{k:02d}" for k, plan_i in enumerate(order)}
        objects = []
        for k, plan_i in enumerate(order):
            parent = items[plan_i].parent
            objects.append(
                _scene_object(
                    items[plan_i],
                    ids[plan_i],
                    k + 1,
                    ids[parent] if parent is not None else None,
                    recipe,
                )
            )
        t = recipe.table
        return SceneSpec(
            meta=SceneMeta(
                scene_id=scene_id,
                generator_version=GENERATOR_VERSION,
                seed=seed,
                recipe_hash=recipe.recipe_hash,
                settled=False,
                difficulty=_difficulty(level, items, zones, lighting, grid, cameras, clearance),
            ),
            table=Table(
                size_xy=t.size_xy,
                height=t.height,
                thickness=t.thickness,
                material_id=t.grid_material_id if grid else t.material_id,
                grid_cell_m=t.grid_cell_m if grid else None,
            ),
            objects=objects,
            gripper=gripper,
            zones=zones,
            cameras=cameras,
            lighting=lighting,
            render=recipe.render.model_copy(),
            physics=recipe.physics.model_copy(),
            questions=recipe.questions.model_copy(deep=True),
        )
    raise LayoutError(
        f"{scene_id} (seed {seed}): no valid layout in {recipe.max_layout_attempts} attempts"
    )
