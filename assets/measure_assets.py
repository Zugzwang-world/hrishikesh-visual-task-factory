"""Measure cached Poly Haven models for assets/manifest.yaml (no Blender).

For each asset id: extents in the vtf.core.assets mesh convention (Z up, centred), the
meta.json dimensions for a cross-check, the triangle count, the area-weighted mean base colour
(linear light) with a suggested colour word, and, with --container, the open interior measured by
ray casting into the mesh (floor height at the centre, rim height, inner radius or inner size at
the rim).

    uv run python assets/measure_assets.py wooden_bowl_02 lemon --container wooden_bowl_02
"""

import argparse
import colorsys
import json
from pathlib import Path

import numpy as np
import trimesh
from rest_pose import REST, rest_file

from vtf.core.assets import GLTF_TO_ZUP, asset_cache_dir

COLOR_WORDS = (
    "red", "green", "blue", "yellow", "orange", "purple", "white", "black", "brown", "grey", "pink"
)  # fmt: skip


def srgb_to_linear(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c: np.ndarray) -> np.ndarray:
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def colour_word(linear_rgb) -> str:
    """A rough colour word for a mean linear colour (a suggestion: check the contact sheet)."""
    r, g, b = (float(v) for v in linear_to_srgb(np.asarray(linear_rgb)))
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    deg = h * 360
    if s < 0.18 or v < 0.12:
        if v > 0.75:
            return "white"
        if v < 0.2:
            return "black"
        return "grey"
    if deg < 15 or deg >= 330:
        return "brown" if v < 0.45 else ("pink" if s < 0.45 else "red")
    if deg < 45:
        return "brown" if v < 0.6 or s < 0.45 else "orange"
    if deg < 70:
        return "yellow" if v > 0.5 else "brown"
    if deg < 170:
        return "green"
    if deg < 255:
        return "blue"
    if deg < 290:
        return "purple"
    return "pink"


def load(asset_id: str, cache: Path):
    """The cached glTF, or its rest-pose variant (assets/rest_pose.py) when one is derived."""
    meta = json.loads((cache / asset_id / "meta.json").read_text())
    path = cache / asset_id / meta["file"]
    if asset_id in REST and rest_file(asset_id, cache).exists():
        path = rest_file(asset_id, cache)
    meta["file"] = path.name
    scene = trimesh.load(str(path), force="scene", process=False)
    return meta, scene


def mean_colour(scene: trimesh.Scene) -> np.ndarray:
    """Area-weighted mean base colour over all faces (texture sampled at face centroids)."""
    total, weight = np.zeros(3), 0.0
    for node in scene.graph.nodes_geometry:
        transform, name = scene.graph[node]
        geom = scene.geometry[name]
        area = geom.area_faces
        visual = geom.visual
        mat = getattr(visual, "material", None)
        factor = np.ones(4)
        if mat is not None and getattr(mat, "baseColorFactor", None) is not None:
            factor = np.asarray(mat.baseColorFactor, dtype=float)
            if factor.max() > 1.0:
                factor = factor / 255.0
        tex = getattr(mat, "baseColorTexture", None) if mat is not None else None
        uv = getattr(visual, "uv", None)
        if tex is not None and uv is not None:
            img = np.asarray(tex.convert("RGB"), dtype=float) / 255.0
            h, w, _ = img.shape
            fuv = uv[geom.faces].mean(axis=1) % 1.0
            px = np.clip((fuv[:, 0] * (w - 1)).astype(int), 0, w - 1)
            py = np.clip(((1 - fuv[:, 1]) * (h - 1)).astype(int), 0, h - 1)
            col = srgb_to_linear(img[py, px]) * factor[:3]
        else:
            col = np.tile(factor[:3], (len(geom.faces), 1))
        total += (col * area[:, None]).sum(axis=0)
        weight += area.sum()
    return total / max(weight, 1e-12)


def local_mesh(scene: trimesh.Scene, scale: float = 1.0) -> trimesh.Trimesh:
    raw = scene.to_geometry()
    mesh = trimesh.Trimesh(vertices=raw.vertices, faces=raw.faces, process=False)
    mesh.apply_transform(GLTF_TO_ZUP)
    mesh.apply_scale(scale)
    lo, hi = mesh.bounds
    mesh.apply_translation(-(lo + hi) / 2.0)
    return mesh


def container_interior(mesh: trimesh.Trimesh) -> dict:
    """Floor at the centre, rim at the top, opening measured just below the rim."""
    lo, hi = mesh.bounds
    height = hi[2] - lo[2]
    # Floor: first hit of a ray straight down the centre (and a small ring, median).
    pts = [(0.0, 0.0)] + [(0.01 * np.cos(a), 0.01 * np.sin(a)) for a in np.linspace(0, 6.28, 8)]
    origins = np.array([(x, y, hi[2] + 0.05) for x, y in pts])
    dirs = np.tile((0.0, 0.0, -1.0), (len(origins), 1))
    locs, ray_idx, _ = mesh.ray.intersects_location(origins, dirs, multiple_hits=False)
    floor = float(np.median(locs[:, 2]) - lo[2]) if len(locs) else 0.0
    # Opening: horizontal rays from the axis outward at three quarters of the wall height
    # (conservative for sloped bowl walls; below handles and uneven rims).
    z = lo[2] + floor + 0.75 * (height - floor)
    angles = np.linspace(0, 2 * np.pi, 72, endpoint=False)
    dirs = np.stack([np.cos(angles), np.sin(angles), np.zeros_like(angles)], axis=1)
    origins = np.tile((0.0, 0.0, z), (len(angles), 1))
    locs, ray_idx, _ = mesh.ray.intersects_location(origins, dirs, multiple_hits=False)
    dist = np.full(len(angles), np.nan)
    dist[ray_idx] = np.linalg.norm(locs[:, :2], axis=1)
    out = {
        "floor_z_m": round(floor, 4),
        "rim_z_m": round(float(height), 4),
        "inner_radius_m": round(float(np.nanpercentile(dist, 10)), 4),
        "radius_median_m": round(float(np.nanmedian(dist)), 4),
        "hit_fraction": round(float(np.isfinite(dist).mean()), 2),
    }
    # Rect interior: half sizes along x and y.
    for axis, idx in (("x", (0, 36)), ("y", (18, 54))):
        out[f"inner_{axis}_m"] = round(float(dist[idx[0]] + dist[idx[1]]), 4)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("ids", nargs="+")
    parser.add_argument("--container", nargs="*", default=[], help="Also measure the interior.")
    parser.add_argument("--scale", nargs="*", default=[], help="id=scale pairs (default 1).")
    args = parser.parse_args()
    scales = {k: float(v) for k, v in (item.split("=") for item in args.scale)}
    cache = asset_cache_dir()
    for asset_id in args.ids:
        meta, scene = load(asset_id, cache)
        mesh = local_mesh(scene, scales.get(asset_id, 1.0))
        rgb = mean_colour(scene)
        row = {
            "id": asset_id,
            "size_m": [round(float(v), 4) for v in mesh.extents],
            "meta_mm": [round(v) for v in (meta.get("dimensions_mm") or [])],
            "scale": scales.get(asset_id, 1.0),
            "triangles": int(len(mesh.faces)),
            "rgb": [round(float(v), 3) for v in rgb],
            "color": colour_word(rgb),
            "file": f"{asset_id}/{meta['file']}",
        }
        if asset_id in args.container:
            row["container"] = container_interior(mesh)
        print(json.dumps(row))


if __name__ == "__main__":
    main()
