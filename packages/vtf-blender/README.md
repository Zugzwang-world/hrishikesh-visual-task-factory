# vtf-blender

Code that runs inside Blender's bundled Python (it imports `bpy`). It reads a scene file written by `vtf`, builds and settles the scene, renders it, and writes images, instance-mask and depth passes, and settled poses back to disk. It never imports `vtf`, and `vtf` never imports it: the two talk only through files.

`scripts/` holds entry points run with Blender in background mode:

```bash
BLENDER=/Applications/Blender.app/Contents/MacOS/Blender
$BLENDER -b --python-exit-code 1 -P packages/vtf-blender/scripts/render_scene.py -- \
    --scene spec/examples/scene_example.json --out out/render_test --device GPU
$BLENDER -b -P packages/vtf-blender/scripts/smoke_render.py
```

`uv run vtf render <run>` (`vtf.render.bridge`) calls `render_scene.py --jobs <jobs.json>` once per batch of scenes (`--batch-size`, default 5), watches its `VTF_SCENE_START` / `VTF_SCENE_DONE` / `VTF_SCENE_FAILED` lines, kills Blender when one scene runs past `--timeout` (default 300 s) and restarts it for the rest, then applies each camera's quality dial core side (`vtf.camera`). Every camera in the scene file is rendered; `scene.glb` is exported unless `--no-gltf`.

`src/vtf_blender/`:

- `build.py`: scene file (plain dicts) to Blender objects: primitives with Principled BSDF, procedural wood table with legs, metal gripper, floor, HDRI world, cameras.
- `settle.py`: Bullet rigid-body drop with the scene's `physics` parameters; settled poses rounded to a micrometre, and the settle report.
- `passes.py`: beauty on the requested device; object index, depth and amodal masks from 1-sample CPU renders (exact on re-render); K and world-to-camera.
- `files.py`: EXR reading with OpenImageIO, PNG writing without metadata, stable JSON.
- `device.py`: GPU (Metal, OptiX, CUDA, HIP, oneAPI) or CPU.
- `export.py`: `scene.glb` for the Task Explorer, display only (not truth): plain materials, 256 px base-colour textures, decimated meshes, a painted table top with the grid and zones, the cameras; under 3 MB. glTF is Y-up: world z is glTF y, world y is glTF -z.

Pass indices: objects 1..999, table top 1000, gripper 1001. Floor, table legs and world are 0, because the scene file does not model them.
