# web: Task Explorer

A static Next.js site (App Router, `output: "export"`) with three.js (react-three-fiber). Two views:

- **Scenes**: pick a scene, step through its questions, and for each one see the camera image, the 3D scene snapped to that camera (drag to orbit), the true answer, every model's answer with a tick or cross, the `shortcut_conflict` flag, and the G1 to G7 gate evidence.
- **Conflict gallery** (`?view=gallery`): the tasks where a 2D shortcut (nearest in the picture, lowest in the frame) gives a different answer from the 3D truth and models took it. Each card shows the camera thumbnail with the true-answer and 2D-shortcut objects marked, the question, the truth against the shortcut answer, which models took the shortcut, and every model's answer. Clicking a card opens that scene at that camera and task.

Marks, in the camera image and the 3D view alike, each with a text label so identity never rests on colour alone:

- blue (`#2a78d6`), "true answer": the object(s) the true answer names (for a yes/no free-space answer, the objects the answer rests on);
- orange (`#eb6834`), "2D shortcut": the object(s) the 2D-shortcut answer names (shortcut-conflict tasks only);
- reddish purple, "wrong pick": an object a model with the image wrongly named;
- white with a dark halo: other evidence objects for the question.

The step dots under a task card are green (every model right), red (none), split (some), or grey (no answers); an orange bar marks shortcut-conflict tasks.

The site reads only `public/data/`, a small sample written by the exporter. Nothing is computed in the browser except drawing (and projecting object centres into the image, with the camera's lens distortion when its quality dial sets one).

## Rebuild the sample

From the repo root, for the v1 run with the overnight model sweep and the findings gallery:

```bash
uv run vtf export out/v1 --format explorer --out web/public/data --scenes 6 \
  --results out/v1/sweep/claude-haiku-5-5 \
  --results out/v1/sweep/claude-sonnet-5-5 \
  --results out/v1/sweep/claude-opus-5-5 \
  --results out/v1/sweep/claude-opus-5-5-xhigh \
  --results out/v1/eval/claude-sonnet-5-5__blind \
  --gallery docs/reports/v1/findings.json
```

- `--results <dir>` (repeatable): a folder with `results.jsonl` and `summary.json`. The model is labelled by the folder name (the sweep key); `index.json` adds a display name and effort from `summary.json` (for example "Opus 5.5 · xhigh"), and a question-only run (`with_image: false`) is marked blind ("Sonnet 5.5 · high · blind"). Models are listed Haiku, Sonnet, Opus, then by effort, blind runs last. A folder that is missing or empty is skipped with a message. Without `--results` the exporter reads `<run>/eval/*/` as before.
- `--gallery <json>`: a file with a `"gallery"` list (`task_id`, `scene_id`, `family`, `camera`, `question`, `truth`, `shortcut_answer`, `models_taking_shortcut`). Its scenes are always exported (counterfactual twins from `<run>/twins/` too), on top of the `--scenes` pick, and `index.json` gets a `gallery` list: each entry plus `truth_object_ids`, `shortcut_object_ids`, the camera, a thumbnail of that camera's image (`thumb_<camera>.jpg`), the marks' positions on it, and every model's answer. Entries whose task is not in the run are listed under `gallery_skipped`.
- `--scenes` (default 6): coverage scenes, picked greedily to cover the most question families, then scenes with wrong model answers; gallery scenes are not picked again.
- `--max-width` (default 1024): camera images are JPEGs (quality 80) at most this wide. `--max-mb` (default 40): size budget; coverage scenes that would exceed it are skipped (gallery scenes never are).
- `--compress-glb/--no-compress-glb` (default on): `scene.glb` goes through `web/scripts/compress-glb.mjs` (glTF-Transform: dedup, prune, WebP textures at most 1024 px, meshopt geometry). It needs Node and `npm install` in `web/`. If compression fails for a scene, the exporter copies the plain glb and says so. No Draco: its decoder would come from a CDN; drei's `useGLTF` decodes meshopt with the decoder bundled in three-stdlib.
- Writes `index.json` and per scene `scene.json`, `<camera>.jpg`, `thumb.jpg`, `thumb_<camera>.jpg` (gallery cameras) and `scene.glb` (when the render lane exported one).
- In `scene.json`, every shortcut-conflict task has `truth_object_ids` and `shortcut_object_ids`, and every model answer has `display_name`, `effort`, `answer_object_ids` and `took_shortcut`. Object ids come from the naming rule (`vtf.core.vocab.resolve_object`, as the scorer resolves answers) against the scene's full object list; when a shortcut name fits several objects (three vases), the object recorded in the program's G1 trace `shortcut` step is used; a name that fits nothing gives `[]`.
- Re-running clears the previous sample (it refuses to clear a folder without `index.json`). The output is a pure function of the run folder and the options, so the same run gives the same files.
- `public/data/` is gitignored: the sample is generated output. Deploys upload it from disk.

The sample above (6 coverage scenes plus 12 gallery scenes) is about 11 MB: 6 MB of compressed glTF (12.8 MB plain), 3.5 MB of JPEGs, 1.7 MB of scene JSON.

Older runs work: a field a v0.1 run lacks (rig, shortcut_conflict, glTF, other cameras, display names) is null or missing, and the viewer shows it as missing (disabled camera tabs, "Shortcut check n/a", primitives instead of glTF, the model key instead of a display name).

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
uv run vtf export out/<run> --format explorer --out web/public/data --scenes 6 [--results ...] [--gallery ...]
cd web
vercel deploy --prod --yes
```

Vercel builds the site (`next build`, static export) and serves `out/`. The production URL is public; preview URLs sit behind a Vercel login. Production: https://vtf-task-explorer.vercel.app

## Frames

The scene file is Blender's world: metres, Z up, quaternions w, x, y, z. three.js is Y up. Blender-frame things (primitives, gripper, frusta, highlight boxes) are drawn in a group rotated -90 degrees about X, which is the same map Blender's glTF exporter applies (x, y, z) -> (x, z, -y), so a `scene.glb` loads as is and lines up. Camera poses come from each camera's OpenCV `world_to_camera` (x right, y down, z forward); `lib/geometry.ts` converts them to three.js (looks down -Z, Y up) and takes the vertical field of view from K.

## Files

- `components/Explorer.tsx`: data loading, view switch, scene strip, camera tabs, image with role pins, task card (stepper, truth and 2D-shortcut answers, every model's answer, gates), conflict gallery.
- `components/SceneView3D.tsx`: the 3D view (glTF or primitives from the object list, gripper, zones, camera frusta, role outlines and labels).
- `lib/highlights.ts`: which objects to mark for a task, in which role, with which words and colours.
- `lib/geometry.ts`: frame conversions and projection. `lib/types.ts`: the export format.
- `scripts/compress-glb.mjs`: meshopt + WebP glTF compression, called by the exporter.
- Exporter: `packages/vtf/src/vtf/package/explorer.py`; test: `tests/test_explorer.py`.
