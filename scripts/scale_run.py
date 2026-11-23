"""Overnight scale run: generate, render, validate, G6 twins and collect in resumable batches.

    nohup uv run python scripts/scale_run.py --recipe recipes/first_scene.yaml --n 500 --seed 1 \\
        --out out/v1 > /dev/null 2>&1 &

Progress: <out>/logs/progress.json (counts, gate pass rates, seconds per scene, ETA) and one line
per batch in <out>/logs/scale_run.log. Kill and restart with the same arguments to resume: no
finished render is redone. The logic lives in vtf.validate.scale (also `uv run vtf run`).
"""

import argparse
from pathlib import Path

from vtf.validate.scale import RENDER_TIMEOUT_S, scale_run


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--recipe", type=Path, required=True, help="Recipe YAML.")
    p.add_argument("--n", type=int, required=True, help="Number of scenes.")
    p.add_argument("--seed", type=int, default=1, help="Run seed.")
    p.add_argument("--out", type=Path, required=True, help="Run folder.")
    p.add_argument("--batch", type=int, default=25, help="Scenes per batch.")
    p.add_argument("--twin-fraction", type=float, default=0.2, help="G6 sample of new tasks.")
    p.add_argument("--no-twins", action="store_true", help="Skip G6 twins.")
    p.add_argument("--max-scenes", type=int, default=None, help="Only the first N scenes.")
    p.add_argument("--device", default="gpu", help="Render device: gpu or cpu.")
    p.add_argument("--retry-failed", action="store_true", help="Re-render failed scenes.")
    p.add_argument("--gltf", action="store_true", help="Ask the renderer for scene.glb exports.")
    p.add_argument("--timeout", type=float, default=RENDER_TIMEOUT_S, help="Seconds per scene.")
    a = p.parse_args()
    scale_run(
        recipe=a.recipe, n=a.n, seed=a.seed, out=a.out, batch=a.batch,
        twin_fraction=a.twin_fraction, twins=not a.no_twins, max_scenes=a.max_scenes,
        device=a.device, retry_failed=a.retry_failed, gltf=a.gltf, timeout=a.timeout,
    )  # fmt: skip


if __name__ == "__main__":
    main()
