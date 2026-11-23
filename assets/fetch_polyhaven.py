"""Download one Poly Haven asset (glTF model or HDRI) into assets/cache/<asset_id>/.

Stdlib only. Uses the public API at https://api.polyhaven.com. Its terms ask every call to
carry a unique User-Agent naming the software, and any app that surfaces content from the
live API to show a "Powered by Poly Haven" credit. The assets themselves are CC0.

    uv run python assets/fetch_polyhaven.py wooden_bowl_02 --kind model --res 1k
    uv run python assets/fetch_polyhaven.py studio_small_09 --kind hdri --res 1k

Models are fetched as glTF (not .blend: .blend files can carry embedded Python).
"""

import argparse
import json
import urllib.request
from pathlib import Path

API = "https://api.polyhaven.com"
USER_AGENT = "vtf-visual-task-factory/0.0.1"
CACHE = Path(__file__).resolve().parent / "cache"


def get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.write_bytes(get(url))
    print(f"{dest.relative_to(CACHE.parent)}  {dest.stat().st_size:,} bytes")


def fetch(asset_id: str, kind: str, res: str) -> Path:
    files = json.loads(get(f"{API}/files/{asset_id}"))
    info = json.loads(get(f"{API}/info/{asset_id}"))
    out = CACHE / asset_id
    if kind == "model":
        entry = files["gltf"][res]["gltf"]
        main = out / Path(entry["url"]).name
        download(entry["url"], main)
        for rel, inc in entry.get("include", {}).items():
            download(inc["url"], out / rel)
    else:
        entry = files["hdri"][res]["hdr"]
        main = out / Path(entry["url"]).name
        download(entry["url"], main)
    meta = {
        "asset_id": asset_id,
        "kind": kind,
        "resolution": res,
        "name": info.get("name"),
        "dimensions_mm": info.get("dimensions"),
        "authors": info.get("authors"),
        "license": "CC0",
        "source": f"https://polyhaven.com/a/{asset_id}",
        "file": main.name,
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return main


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("asset_id")
    parser.add_argument("--kind", choices=["model", "hdri"], required=True)
    parser.add_argument("--res", default="1k")
    args = parser.parse_args()
    print(fetch(args.asset_id, args.kind, args.res))
