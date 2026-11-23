"""Asset manifest (assets/manifest.yaml) and exact object meshes for every lane.

The manifest lists every object the sampler may place: Poly Haven models (CC0, fetched into the
asset cache as glTF) and procedural primitives of exact size used as scale references. Each entry
records the real-world size, the category noun used in questions, a colour word, the collision
proxy for the settle, and container geometry for containment questions.

Mesh convention (shared with packages/vtf-blender, which must build the same mesh):

1. Load the glTF with every node transform applied and join all meshes.
2. Convert glTF +Y up to Blender +Z up: (x, y, z) -> (x, -z, y). Blender's glTF importer does
   the same conversion.
3. Scale by `entry.scale` (times the SceneObject's own `scale`).
4. Translate so the bounding-box centre is at the origin.

The result is the object's local frame: `SceneObject.pose` places its bounding-box centre and
`SceneObject.dimensions` are its full extents, exactly as for primitives. `object_mesh` returns
the world-frame mesh of any SceneObject (primitive or asset), for the ray caster, programs and
gates. trimesh is used for loading and as the mesh type; it never touches bpy.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

import numpy as np
import trimesh
import yaml
from pydantic import BaseModel, ConfigDict, Field

from vtf.core.geometry import Vec3
from vtf.core.scene import SceneObject

ASSET_MANIFEST_VERSION = "0.1.0"
REPO_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_MANIFEST = REPO_ROOT / "assets" / "manifest.yaml"
PRIMITIVE_SEGMENTS = 64  # matches packages/vtf-blender build.SEGMENTS

# glTF (+Y up) -> Blender (+Z up): (x, y, z) -> (x, -z, y)
GLTF_TO_ZUP = np.array(
    [[1.0, 0.0, 0.0, 0.0], [0.0, 0.0, -1.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0]]
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContainerInfo(_Model):
    """Interior of an open container (bowl, tray, basket), in the asset's centred local frame.

    An object is inside when its centre lies within the interior footprint and above the floor,
    and its lowest point is below the rim (the programs own the exact rule and margins).
    """

    shape: Literal["circle", "rect"] = "circle"
    inner_radius_m: float | None = Field(default=None, gt=0, description="circle interior")
    inner_size_xy: tuple[float, float] | None = Field(default=None, description="rect interior")
    floor_z_m: float = Field(description="Interior floor height above the asset's lowest point.")
    rim_z_m: float = Field(description="Rim height above the asset's lowest point.")
    capacity: int = Field(default=3, ge=1, description="Most objects the sampler puts inside.")


class AssetEntry(_Model):
    id: str = Field(description="Poly Haven asset id, or proc_<shape>_<size> for procedural.")
    source: Literal["polyhaven", "procedural"]
    category: str = Field(description="Singular noun used in questions, e.g. mug.")
    plural: str | None = Field(default=None, description="Plural noun; default category + s.")
    color_name: str | None = Field(
        default=None, description="Colour word for a textured asset (None: no colour word)."
    )
    rgb: Vec3 = Field(description="Representative linear-light colour (procedural: base colour).")
    file: str | None = Field(
        default=None, description="glTF path relative to the asset cache (Poly Haven only)."
    )
    shape: str | None = Field(
        default=None, description="Procedural primitive: box, cylinder, sphere or cone."
    )
    scale: float = Field(default=1.0, gt=0, description="Applied to the file's mesh.")
    size_m: Vec3 = Field(description="Real extents x, y, z (Z up) after `scale`, metres.")
    collision: Literal["convex_hull", "box", "mesh", "primitive"] = Field(
        description="Settle collision proxy. mesh is for passive open containers only."
    )
    passive: bool = Field(
        default=False, description="Does not move in the settle (open containers)."
    )
    container: ContainerInfo | None = None
    can_support: bool = Field(default=False, description="Flat top that can carry a stack.")
    stackable: bool = Field(default=False, description="May be placed on another object.")
    can_go_in_container: bool = Field(default=False)
    lookalike_group: str | None = Field(
        default=None, description="Assets that are easy to confuse (same colour or category)."
    )
    triangles: int | None = Field(default=None, description="Triangle count of the joined mesh.")
    license: str = "CC0"
    url: str | None = None
    notes: str = ""


class AssetManifest(_Model):
    manifest_version: str = ASSET_MANIFEST_VERSION
    cache_dir: str = Field(
        default="assets/cache",
        description="Asset cache relative to the repo root; $VTF_ASSET_CACHE overrides it.",
    )
    assets: list[AssetEntry] = Field(default_factory=list)

    def get(self, asset_id: str) -> AssetEntry:
        for a in self.assets:
            if a.id == asset_id:
                return a
        raise KeyError(f"asset {asset_id!r} not in the asset manifest")

    def by_category(self) -> dict[str, list[AssetEntry]]:
        out: dict[str, list[AssetEntry]] = {}
        for a in self.assets:
            out.setdefault(a.category, []).append(a)
        return out


def asset_cache_dir(manifest: AssetManifest | None = None) -> Path:
    env = os.environ.get("VTF_ASSET_CACHE")
    if env:
        return Path(env)
    rel = manifest.cache_dir if manifest else "assets/cache"
    return REPO_ROOT / rel


@lru_cache(maxsize=4)
def _load_manifest_cached(path: str, mtime: float) -> AssetManifest:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return AssetManifest.model_validate(data)


def load_asset_manifest(path: Path | str | None = None) -> AssetManifest:
    p = Path(path) if path else Path(os.environ.get("VTF_ASSET_MANIFEST", DEFAULT_MANIFEST))
    return _load_manifest_cached(str(p), p.stat().st_mtime)


def _join(scene_or_mesh) -> trimesh.Trimesh:
    if isinstance(scene_or_mesh, trimesh.Scene):
        return scene_or_mesh.to_geometry()
    return scene_or_mesh


@lru_cache(maxsize=128)
def _asset_local_mesh(cache_dir: str, file: str, scale: float) -> trimesh.Trimesh:
    raw = _join(trimesh.load(str(Path(cache_dir) / file), force="scene", process=False))
    mesh = trimesh.Trimesh(vertices=raw.vertices, faces=raw.faces, process=False)
    mesh.apply_transform(GLTF_TO_ZUP)
    mesh.apply_scale(scale)
    lo, hi = mesh.bounds
    mesh.apply_translation(-(lo + hi) / 2.0)
    return mesh


def primitive_mesh(shape: str, dims: Vec3) -> trimesh.Trimesh:
    """A primitive centred on its bounding box with full extents `dims` (as Blender builds it)."""
    dx, dy, dz = (float(v) for v in dims)
    if shape == "box":
        mesh = trimesh.creation.box(extents=(dx, dy, dz))
    elif shape == "cylinder":
        mesh = trimesh.creation.cylinder(radius=0.5, height=1.0, sections=PRIMITIVE_SEGMENTS)
        mesh.apply_scale((dx, dy, dz))
    elif shape == "cone":
        mesh = trimesh.creation.cone(radius=0.5, height=1.0, sections=PRIMITIVE_SEGMENTS)
        mesh.apply_translation((0, 0, -0.5))  # base at local z = -0.5, apex at +0.5
        mesh.apply_scale((dx, dy, dz))
    elif shape == "sphere":
        mesh = trimesh.creation.uv_sphere(
            radius=0.5, count=(PRIMITIVE_SEGMENTS // 2, PRIMITIVE_SEGMENTS)
        )
        mesh.apply_scale((dx, dy, dz))
    else:
        raise ValueError(f"unknown primitive shape {shape!r}")
    return mesh


def asset_local_mesh(entry: AssetEntry, scale: float = 1.0, manifest=None) -> trimesh.Trimesh:
    """The asset's mesh in its centred, Z-up local frame (a copy, safe to modify)."""
    if entry.source == "procedural":
        size = tuple(v * scale for v in entry.size_m)
        return primitive_mesh(entry.shape or "box", size)
    if not entry.file:
        raise ValueError(f"asset {entry.id} has no file")
    cache = str(asset_cache_dir(manifest))
    return _asset_local_mesh(cache, entry.file, round(entry.scale * scale, 9)).copy()


def object_local_mesh(obj: SceneObject, manifest: AssetManifest | None = None) -> trimesh.Trimesh:
    if obj.source == "primitive":
        return primitive_mesh(obj.shape, obj.dimensions)
    manifest = manifest or load_asset_manifest()
    return asset_local_mesh(manifest.get(obj.shape), obj.scale, manifest)


def object_mesh(obj: SceneObject, manifest: AssetManifest | None = None) -> trimesh.Trimesh:
    """World-frame mesh of a SceneObject at its pose (primitives and assets alike)."""
    mesh = object_local_mesh(obj, manifest)
    mesh.apply_transform(obj.pose.matrix())
    return mesh
