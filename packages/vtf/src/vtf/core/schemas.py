"""Export the contract JSON Schemas to spec/.

uv run python -m vtf.core.schemas          # writes spec/*.schema.json
"""

import json
import sys
from pathlib import Path

from pydantic import BaseModel

from vtf.core.assets import ASSET_MANIFEST_VERSION, AssetManifest
from vtf.core.render_manifest import MANIFEST_VERSION, RenderManifest
from vtf.core.scene import SPEC_VERSION, SceneSpec
from vtf.core.task import TASK_VERSION, TaskPackage

SCHEMAS: dict[str, tuple[type[BaseModel], str]] = {
    "scene.schema.json": (SceneSpec, SPEC_VERSION),
    "render_manifest.schema.json": (RenderManifest, MANIFEST_VERSION),
    "task_package.schema.json": (TaskPackage, TASK_VERSION),
    "asset_manifest.schema.json": (AssetManifest, ASSET_MANIFEST_VERSION),
}

REPO_ROOT = Path(__file__).resolve().parents[5]
SPEC_DIR = REPO_ROOT / "spec"


def schema_text(model: type[BaseModel], version: str) -> str:
    schema = model.model_json_schema(by_alias=True)
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$comment": f"Generated from vtf.core by `uv run python -m vtf.core.schemas`. "
        f"Format version {version}.",
        **schema,
    }
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def export_schemas(spec_dir: Path = SPEC_DIR) -> list[Path]:
    spec_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, (model, version) in SCHEMAS.items():
        path = spec_dir / name
        path.write_text(schema_text(model, version), encoding="utf-8")
        paths.append(path)
    return paths


if __name__ == "__main__":
    for p in export_schemas(Path(sys.argv[1]) if len(sys.argv) > 1 else SPEC_DIR):
        print(p)
