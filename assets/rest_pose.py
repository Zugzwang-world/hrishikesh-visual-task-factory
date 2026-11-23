"""Derive rest-pose glTF files for assets modelled standing on end (stdlib only).

Some Poly Haven models are modelled upright (a screwdriver on its handle, a lemon on its tip).
The sampler places assets in their modelled orientation (yaw only), so those would topple in the
settle. For each asset in REST this writes `<name>_rest.gltf` next to the cached glTF: the same
buffers and textures, with one new root node that turns the model onto its side. The manifest's
`file` points at the derived file, so vtf.core.assets and vtf-blender both load the lying model.

A rotation about x is the same in glTF (+Y up) and Blender (+Z up) frames, because the frame
change is itself a rotation about x.

    uv run python assets/rest_pose.py            # derive every REST asset that is cached
"""

import json
import math
import sys
from pathlib import Path

CACHE = Path(__file__).resolve().parent / "cache"

# asset id -> degrees about the x axis that lay it on its side
REST = {
    "lemon": 90.0,
    "food_kiwi_01": 90.0,
    "food_avocado_01": 90.0,
    "screwdriver": 90.0,
    "flathead_screwdriver": 90.0,
    "cross_pein_hammer": 90.0,
    "wooden_hammer_01": 90.0,
    "adjustable_wrench": 90.0,
    "pliers": 90.0,
}


def rest_file(asset_id: str, cache: Path = CACHE) -> Path:
    meta = json.loads((cache / asset_id / "meta.json").read_text())
    src = cache / asset_id / meta["file"]
    return src.with_name(src.stem + "_rest.gltf")


def derive(asset_id: str, degrees: float, cache: Path = CACHE) -> Path:
    meta = json.loads((cache / asset_id / "meta.json").read_text())
    src = cache / asset_id / meta["file"]
    gltf = json.loads(src.read_text())
    half = math.radians(degrees) / 2
    root = {
        "name": "vtf_rest_pose",
        "rotation": [math.sin(half), 0.0, 0.0, math.cos(half)],  # glTF quaternion x, y, z, w
    }
    scene = gltf["scenes"][gltf.get("scene", 0)]
    root["children"] = list(scene["nodes"])
    gltf["nodes"].append(root)
    scene["nodes"] = [len(gltf["nodes"]) - 1]
    dest = rest_file(asset_id, cache)
    dest.write_text(json.dumps(gltf, indent=1) + "\n")
    return dest


def main(ids: list[str]) -> None:
    for asset_id in ids or list(REST):
        if not (CACHE / asset_id / "meta.json").exists():
            print(f"{asset_id}: not cached, skipped")
            continue
        print(derive(asset_id, REST[asset_id]).relative_to(CACHE.parent))


if __name__ == "__main__":
    main(sys.argv[1:])
