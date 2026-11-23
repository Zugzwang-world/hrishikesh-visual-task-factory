# spec

Versioned, renderer-independent contracts. The JSON Schemas are generated from the pydantic models in `packages/vtf/src/vtf/core/` with `uv run python -m vtf.core.schemas`; a test fails if they go stale.

- `scene.schema.json`: SceneSpec, the world state (table, objects, gripper, zones, cameras, lighting, render and physics settings, seed). Every answer is computed from this file.
- `render_manifest.schema.json`: what the renderer writes per scene (beauty, 16-bit object index, planar depth in metres, amodal masks, K and world-to-camera in the OpenCV convention, settle report, timings).
- `task_package.schema.json`: one task (image, question, program, typed answer, evidence objects, gate results, versions).

Conventions: metres; Blender's right-handed Z-up world; quaternions in w, x, y, z order; table centred on the origin with its top at z = table.height; object pass indices 1..999 (table 1000, gripper 1001, background 0). The run folder layout is documented in `packages/vtf/src/vtf/core/layout.py`.

`examples/` holds `scene_example.json` and `run_example/`, a complete tiny run made by `examples/make_fixtures.py` with the analytic ray caster (`vtf.core.raycast`), so every lane can work against the contracts without Blender.
