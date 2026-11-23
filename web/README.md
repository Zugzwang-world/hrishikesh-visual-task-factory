# web: Task Explorer

A static Next.js site (App Router, `output: "export"`) with three.js (react-three-fiber). Pick a scene, step through its questions, and for each one see the camera image, the 3D scene snapped to that camera (drag to orbit), the true answer, every model's answer with a tick or cross, the `shortcut_conflict` flag, and the G1 to G7 gate evidence. Evidence objects are outlined in orange in both views; an object a model wrongly named is outlined in red.

The site reads only `public/data/`, a small sample written by the exporter. Nothing is computed in the browser except drawing.

## Rebuild the sample

From the repo root:

```bash
uv run vtf export out/s2 --format explorer --out web/public/data --scenes 6
```

- `--scenes` (default 6): scenes in the sample, picked greedily to cover the most question families, then scenes with wrong model answers.
- `--max-width` (default 1024): camera images are JPEGs at most this wide. `--max-mb` (default 25): size budget; scenes that would exceed it are skipped.
- Writes `index.json` and per scene `scene.json`, `<camera>.jpg`, `thumb.jpg` and `scene.glb` (copied from `render/scene.glb` when the render lane exported one).
- Re-running clears the previous sample (it refuses to clear a folder without `index.json`). The output is a pure function of the run folder, so the same run gives the same files.
- `public/data/` is gitignored: the sample is generated output. Deploys upload it from disk.

Older runs work: a field a v0.1 run lacks (rig, shortcut_conflict, glTF, other cameras) is null, and the viewer shows it as missing (disabled camera tabs, "Shortcut check n/a", primitives instead of glTF).

## Run locally

```bash
cd web
npm install
npm run dev          # http://localhost:3000
npm run build        # static site in web/out/
npx serve out        # serve the build
```

Node 24 or newer (the repo pins 26 in `.node-version`).

## Deploy (Vercel)

The Vercel project is `vtf-task-explorer`, linked from `web/` (`web/.vercel/` is gitignored, so on a fresh checkout run `vercel link --yes --project vtf-task-explorer` first).

```bash
uv run vtf export out/<run> --format explorer --out web/public/data --scenes 6
cd web
vercel deploy --prod --yes
```

Vercel builds the site (`next build`, static export) and serves `out/`. The production URL is public; preview URLs sit behind a Vercel login. Production: https://vtf-task-explorer.vercel.app

## Frames

The scene file is Blender's world: metres, Z up, quaternions w, x, y, z. three.js is Y up. Blender-frame things (primitives, gripper, frusta) are drawn in a group rotated -90 degrees about X, which is the same map Blender's glTF exporter applies (x, y, z) -> (x, z, -y), so a `scene.glb` loads as is and lines up. Camera poses come from each camera's OpenCV `world_to_camera` (x right, y down, z forward); `lib/geometry.ts` converts them to three.js (looks down -Z, Y up) and takes the vertical field of view from K.

## Files

- `components/Explorer.tsx`: data loading, scene strip, camera tabs, image with evidence pins, task card (stepper, answers, gates).
- `components/SceneView3D.tsx`: the 3D view (glTF or primitives from the object list, gripper, zones, camera frusta, labels).
- `lib/geometry.ts`: frame conversions and projection. `lib/types.ts`: the export format.
- Exporter: `packages/vtf/src/vtf/package/explorer.py`; test: `tests/test_explorer.py`.
