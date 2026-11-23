# vtf-blender

Code that runs inside Blender's bundled Python (it imports `bpy`). It reads a scene file written by `vtf`, builds and settles the scene, renders it, and writes images, instance-mask and depth passes, and settled poses back to disk. It never imports `vtf`, and `vtf` never imports it: the two talk only through files.

`scripts/` holds entry points run with Blender in background mode:

```bash
BLENDER=/Applications/Blender.app/Contents/MacOS/Blender
$BLENDER -b --python-exit-code 1 -P packages/vtf-blender/scripts/render_scene.py -- \
    --scene spec/examples/scene_example.json --out out/render_test --device GPU
$BLENDER -b -P packages/vtf-blender/scripts/smoke_render.py
```

`uv run vtf render <run>` calls `render_scene.py` once per scene (`vtf.render.bridge`).

`src/vtf_blender/`:

- `build.py`: scene file (plain dicts) to Blender objects: primitives with Principled BSDF, procedural wood table with legs, metal gripper, floor, HDRI world, cameras.
- `settle.py`: Bullet rigid-body drop with the scene's `physics` parameters; settled poses rounded to a micrometre, and the settle report.
- `passes.py`: beauty on the requested device; object index, depth and amodal masks from 1-sample CPU renders (exact on re-render); K and world-to-camera.
- `files.py`: EXR reading with OpenImageIO, PNG writing without metadata, stable JSON.
- `device.py`: GPU (Metal, OptiX, CUDA, HIP, oneAPI) or CPU.

Pass indices: objects 1..999, table top 1000, gripper 1001. Floor, table legs and world are 0, because the scene file does not model them.
