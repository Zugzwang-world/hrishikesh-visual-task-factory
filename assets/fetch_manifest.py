"""Fill the asset cache for every Poly Haven entry in assets/manifest.yaml (once per machine).

Fetches each missing model (1k glTF) with fetch_polyhaven.py, derives the rest-pose files that
some entries point at (rest_pose.py), and fetches the HDRIs passed with --hdri. --ids limits the
models to a few entries (vtf quickstart fetches only what recipes/quick.yaml uses).

    uv run python assets/fetch_manifest.py [--hdri studio_small_09 ...] [--ids lemon ...]
"""

import argparse

from fetch_polyhaven import CACHE, fetch
from rest_pose import REST, derive

from vtf.core.assets import load_asset_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hdri", nargs="*", default=["studio_small_09"])
    parser.add_argument("--ids", nargs="*", default=None, help="Only these manifest entries.")
    args = parser.parse_args()
    for entry in load_asset_manifest().assets:
        if args.ids is not None and entry.id not in args.ids:
            continue
        if entry.source != "polyhaven" or (CACHE / entry.file).exists():
            continue
        if not (CACHE / entry.id / "meta.json").exists():
            fetch(entry.id, "model", "1k")
        if entry.id in REST:
            derive(entry.id, REST[entry.id])
        print(entry.id, "ok" if (CACHE / entry.file).exists() else "MISSING")
    for hdri in args.hdri:
        if not (CACHE / hdri / "meta.json").exists():
            fetch(hdri, "hdri", "1k")


if __name__ == "__main__":
    main()
