"""Asset checks: the JSON mirror of the manifest is current, and every asset Blender builds has the
same local mesh bounds as vtf.core.assets and comes to rest in the settle.

The Blender test is skipped when Blender or the asset cache is missing (CI has neither).
"""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from vtf.core.assets import asset_cache_dir, asset_local_mesh, load_asset_manifest
from vtf.render.bridge import find_blender

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "assets"))
from export_manifest_json import JSON_PATH, manifest_json_text  # noqa: E402


def _blender() -> str | None:
    try:
        return find_blender()
    except FileNotFoundError:
        return None


def _cached_ids() -> list[str]:
    manifest = load_asset_manifest()
    cache = asset_cache_dir(manifest)
    return [a.id for a in manifest.assets if a.source == "procedural" or (cache / a.file).exists()]


BLENDER = _blender()


def test_manifest_json_is_current() -> None:
    assert JSON_PATH.read_text(encoding="utf-8") == manifest_json_text(), (
        "assets/manifest.json is stale: run `uv run python assets/export_manifest_json.py`"
    )


def test_manifest_entries_are_consistent() -> None:
    manifest = load_asset_manifest()
    ids = [a.id for a in manifest.assets]
    assert len(ids) == len(set(ids))
    assert 25 <= len(ids) <= 45
    colours = {"red", "green", "blue", "yellow", "orange", "purple", "white", "black", "brown",
               "grey", "pink"}  # fmt: skip
    for a in manifest.assets:
        assert a.color_name is None or a.color_name in colours, a.id
        if a.container is not None:
            assert a.passive and a.collision == "mesh", a.id
            assert 0 <= a.container.floor_z_m < a.container.rim_z_m <= a.size_m[2] + 1e-6, a.id
        if a.source == "procedural":
            assert a.shape in ("box", "cylinder", "sphere", "cone") and a.collision == "primitive"


@pytest.mark.skipif(BLENDER is None, reason="Blender is not available")
def test_blender_meshes_match_core_and_settle(tmp_path) -> None:
    ids = _cached_ids()
    if not any(a for a in ids if not a.startswith("proc_")):
        pytest.skip("no Poly Haven asset is cached")
    proc = subprocess.run(
        [BLENDER, "-b", "--factory-startup", "--python-exit-code", "1",
         "-P", str(REPO / "assets" / "blender_asset_check.py"), "--",
         "--out", str(tmp_path), "--ids", *ids],
        capture_output=True, text=True, timeout=600,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    results = json.loads((tmp_path / "assets_check.json").read_text())
    manifest = load_asset_manifest()
    for asset_id in ids:
        entry = manifest.get(asset_id)
        row = results[asset_id]
        extents = asset_local_mesh(entry, manifest=manifest).extents
        np.testing.assert_allclose(row["blender_size_m"], extents, atol=1e-4, err_msg=asset_id)
        np.testing.assert_allclose(row["blender_centre_m"], 0.0, atol=1e-5, err_msg=asset_id)
        settle = row["settle"]
        assert not settle["moving"], (asset_id, settle)
        assert not settle["off_table"], (asset_id, settle)
        assert settle["displacement_m"] < 0.01, (asset_id, settle)
        assert -1e-4 <= settle["support_gap_m"] <= 2e-3, (asset_id, settle)
