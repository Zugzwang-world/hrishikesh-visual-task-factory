"""Write assets/manifest.json from assets/manifest.yaml (validated by vtf.core.assets).

Blender's bundled Python has no YAML reader, so vtf-blender reads this JSON mirror. Run after
every edit of the YAML; tests/test_assets.py checks the mirror is current.

    uv run python assets/export_manifest_json.py
"""

import json
from pathlib import Path

from vtf.core.assets import DEFAULT_MANIFEST, load_asset_manifest

JSON_PATH = Path(__file__).resolve().parent / "manifest.json"


def manifest_json_text(yaml_path: Path = DEFAULT_MANIFEST) -> str:
    manifest = load_asset_manifest(yaml_path)
    data = manifest.model_dump(mode="json")
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    JSON_PATH.write_text(manifest_json_text(), encoding="utf-8")
    print(JSON_PATH)
