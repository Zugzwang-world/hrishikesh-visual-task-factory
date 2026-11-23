# DECISIONS

A dated log, newest first. Each entry says what was decided and why. Reversals get a new entry rather than an edit.

## 23 Nov 2026: Stage 3 lane decisions (brief 05)

| Decision | Choice | Why |
| --- | --- | --- |
| Sampler v1 is separate | `sampler: v1` in a recipe selects it; first_scene.yaml still runs v0 byte for byte | Stage-2 runs and their G7 hashes stay valid. |
| v1 table | 1.2 x 0.8 m (stage-2 style recipes keep 1.0 x 0.7 m); table-level objects use at most 40% of the area | 25 real-size objects fit; high clutter draws smaller items instead of failing layouts. |
| Unique names by construction | No two objects share category and colour except one planned duplicate group and small/large size pairs; zone colours are kept off every object | Every object a question may name stays nameable. |
| Stacks and containers | Stacks two high; passive containers sit exactly on the table; contents filtered by fit | Plausible rests and short settles. |
| Upright assets | Nine assets modelled upright point at pre-rotated rest-pose glTF files (assets/rest_pose.py), used by both Blender and vtf.core.assets | Core meshes and renders stay identical; no double tilt in the sampler. |
| Wrist camera | Beside the palm, looking straight down the approach axis, 70 to 90 degree field of view | Objects under the gripper fill the view; gripper_view questions are fair by construction. |
| Mesh ray caster | Embree per object inside its projected box, exact float64 hits; 0.1 to 0.5 s per camera | Fast enough to check every scene against the render. |
| Camera dial | Post-render in vtf.camera, seeded; k1 distortion followed by the smallest zoom that leaves no black border; index, depth and masks warped by nearest neighbour from the same map as the image; noise sigma rises as 3 x (1/gain - 1) | Every pass stays aligned with the image the model sees; the manifest K stays pinhole and vtf.camera holds the exact map and inverse. |
| G2 and distortion | G2 back-projects through the dial's exact inverse, and only when the manifest says the dial ran | A renderer without the dial leaves the passes unwarped. |
| Batched rendering | Several scenes per Blender process, a 5-minute per-scene watchdog that kills and restarts Blender, refill after a kill; a scene is skipped only when its manifest matches the scene file and the dial | Start-up saved; one stuck scene never stalls a batch; no stale renders. |
| Amodal masks | Objects whose projected box misses a camera get an empty mask without rendering; the rest render only inside their box (bit-identical to full frame) | About half the mask time; masks were not the bottleneck but the saving is free. |
| scene.glb | Display only: plain materials, 256 px textures, triangle budget halved until under 1 MB, dropped above 3 MB; a failed export never fails a render | Small enough for the Task Explorer and the disk budget. |
| Lean renders | Scale runs keep no beauty_raw.png; task images are hard links; depth is compressed | Disk is the binding constraint. |
| Free-disk floor (founder, 23 Nov evening) | The scale driver checks free space before every batch and stops at the last finished batch if the next one would leave less than 20 GB; exit 0, resumable | The founder's rule for this Mac. |
| Incremental validation | Per-scene results under scenes/<id>/validate/, keyed by the scene, manifest, settled scene and code versions; collect rebuilds the run files | Batches validate as they finish; a partial run is always usable. |
| G4 inside programs | Programs apply the spec's metric and pixel margins (front rig stricter) and G4 re-checks them from the result | Near-ties never become tasks; the gate keeps the evidence. |
| G6 policy | A stratified sample by family x rig (at least one per stratum, seeded by task id); only a valid twin whose answer did not change rejects the original; a broken twin is recorded twin_invalid | A bad twin edit is not evidence against the question. |
| Twin rules beyond the spec | Fallback edits when the spec's move is impossible (for example move the nearer object behind for front_to_back, hide another object for occlusion) | More families get a valid twin. |
| free_space rule | Yes needs gap >= the item's largest extent + 2 cm; no needs gap <= its smallest width - 1.5 cm; a corridor as wide as the item between a and b's closest points | The spec's single width was ambiguous for long items. |
| relation candidates | Objects stacked on, inside or under the anchor are excluded | Otherwise a bowl's contents answer "left of the bowl". |
| Scorer resolution | An answer's colour, size and category words must pick out exactly the truth object; a missing word never excludes; a bare number is centimetres; prompts version 0.2.0 | Fair to models without loosening exact answers. |
| G5 rebalance | Seeded rejection sampling: bool 45 to 55%, any single answer value at most 35% (relaxed to 1/k + 5% when a family has few values); twin-linked tasks dropped last; run `vtf balance` after the last collect | Answer priors cannot be exploited; twin pairs survive. |
| Blind baseline | Question only, no image, best-guess prompt, results under `<model>__blind` with their own cache key | A refusal must not inflate vision necessity; blind and image runs never mix. |
| Eval sampling | Stratified with equal shares per stratum; seeded bootstrap intervals | Similar interval widths per family and rig. |
| Audit page | One static page, JPEG images, answers in localStorage with export; object questions offer tap buttons with every scene object plus free text | Works offline on a phone; fewer naming mismatches. |
| Dataset report | Hand-written inline SVG charts, no external fetch | Opens from file:// for a judge. |
| Task Explorer | Static Next.js export with three.js, deployed to the Vercel production domain vtf-task-explorer.vercel.app; data files generated and uploaded, not committed | Public without a server; the repo stays light. |
| Twin mat | A3 single sheet or four A4 tiles; 40 x 28 cm grid, ArUco DICT_4X4_50 ids 0 to 3 in the corners, 31 x 19 cm placement area; one fixed front phone spot and an overhead spot | Most home printers do A4; the stand stays put for a whole photo session. |
| Shopping list | 15 kinds, 23 pieces, one supermarket trip; trays, plates, the basket, jugs and long items left out (wider than the placement area) | Everything on the list fits the mat. |

## 23 Nov 2026: Stage 3 overnight run (brief 05), contracts and lanes

| Decision | Choice | Why |
| --- | --- | --- |
| Contracts v0.2 first | SceneSpec 0.2.0 (asset objects, size words, placement intent, grid table, reach zones, camera rigs with a quality dial, key light, question mix, scene difficulty, twin_of), TaskPackage 0.2.0 (rig, question_alt, shortcut_conflict, scene_objects, twin ids), RenderManifest 0.2.0 (depth.npz, rig, quality, beauty_raw, scene.glb), program FAMILIES registry, number answers, TwinEdit | Eight lanes build in parallel; they can only do that against one agreed contract. |
| Family semantics as a contract | spec/families.md fixes, per family, the truth rule, the G2 pixel route, the G4 margins, the 2D shortcut heuristic and the twin rule | The questions lane (G1) and the validation lane (G2) build the two independent routes at the same time; both need the same definition. |
| shortcut_conflict definition | Per spatial family, the heuristic answer computed by projection from the scene file (image-plane distance, or lower-in-image for depth) compared with the 3D truth; recorded true or false on every spatial task, None otherwise | Decision 2 of brief 05; computing it from the scene file keeps it exact and model-free. |
| Count twins | A count twin removes one counted object (TwinEdit.remove) instead of moving it | Moving an object cannot change a count; removal is the smallest edit that must change the answer. |
| Scoring names | An object answer is right when its colour, size and category words pick out exactly the truth object among task.scene_objects | With unique-by-minimal-name questions, "the red mug" must count as right when only one mug exists. |
| Asset meshes in core | vtf.core.assets loads each glTF with trimesh, converts glTF +Y up to +Z up, scales, centres on the bounding box; object_mesh gives any object's world mesh. trimesh, embreex and rtree added to the core package | Programs, the mesh ray caster and G2 all need the same exact geometry without bpy; Embree casts 786k rays in 0.15 s, so the ray caster stays fast. |
| Depth as depth.npz | np.savez_compressed float32 (exact), 0.7 MB instead of 3.0 MB per camera; read_depth reads both | Only about 17 GB of disk is free; 500 scenes x 3 cameras would otherwise use about 4.5 GB on depth alone. |
| Task images as hard links | link_or_copy instead of copying the beauty PNG into every task folder | Same disk reason; the bytes are identical. |
| Shared resources across worktrees | The main checkout holds the asset cache (assets/cache, symlinked into every worktree), the eval cache (out/cache/eval_cache.sqlite via VTF_EVAL_CACHE), the stage-2 run (out/s2) and the scale run (out/v1) | Never re-download or re-evaluate what exists; outputs survive worktree clean-up. |
| Lanes | Eight builders in worktrees: render assets and look; render cameras and pipeline; sampler; questions; validation (G2, G4, G6, incremental validate, scale driver); report and audit (G5, P16, P17); web (Task Explorer on Vercel); twins (mat, layouts, shopping list). CLAUDE.md lane table updated | The brief's lanes, with render and validation each split in two so no builder carries more than about three hours of work. |
| Keep awake | The session holds a keep-awake and a 14-hour `caffeinate -ims` assertion | The scale run must not stop when the Mac idles; this changes no settings. |

## 23 Nov 2026: Finish setup (brief 03)

| Decision | Choice | Why |
| --- | --- | --- |
| **Determinism (closes OPEN item 6)** | G7 checks scene files, answers, masks and depth for exact equality, comparing decoded pixels rather than file bytes. Images are compared with SSIM and pass at 0.999 or above. GPU stays the default device. A CPU device option gives byte-identical images when they are wanted, for example `vtf verify` on stage. | The setup run found same-seed GPU renders differ by one colour level in 2 of 307,200 pixels, while masks, depth and CPU renders are exact. Masks, depth and the scene file carry all the truth, and G2 recomputes answers from them. Rule C, the G7 row and the "Repeatable" requirement row in BRIEF.md now say exactly this. |
| Device option | `vtf_blender.device.configure_device("GPU" or "CPU")`, shared by every Blender script; `--device` on the smoke scripts and on the `vtf render` and `vtf verify` stubs | One implementation of the GPU/CPU choice. It tries Metal, then OptiX, CUDA, HIP and oneAPI, so the same code runs on the DGX Spark or a cloud GPU. |
| Node.js | Node 26 LTS (26.13.1) installed with fnm and pinned for this repo in `.node-version`; the machine default stays Node 24 | Node 26 is the active LTS since Oct 2026 and Node 24 is in maintenance. fnm switches on `cd`, so other projects on this Mac are untouched. |
| Vercel CLI | 66.6.0, installed under both Node 24 and Node 26; already logged in as zugzwang-world | No new login was needed. The web app itself waits for P25. |
| Hugging Face CLI | `hf` 2.4.0 from `uv tool install huggingface_hub` | The current CLI is `hf`, which replaced `huggingface-cli`. Dataset repos are created with `hf repos create <id> --type dataset --public`. |
| Hugging Face account and dataset | Account Hrishi1802; empty public dataset repo `Hrishi1802/hrishikesh-visual-task-factory`. The write token lives only in the gitignored `.env` (`HF_TOKEN`) and the local hf cache. | Images go to Hugging Face, code to GitHub, as the brief says. The token was shared in chat, so rotate it after the hackathon. |
| Python libraries | Runtime libraries in `packages/vtf`: pydantic, typer, numpy, opencv-python-headless, pillow, pyyaml, jinja2, httpx, scikit-image, python-dotenv. hypothesis and pytest sit in the workspace dev group. | The headless OpenCV build has the same API, ArUco included, without GUI libraries, so it suits Docker and CI. Test tools stay out of the runtime package. python-dotenv loads `.env`. `vtf-blender` still imports only what Blender ships (bpy, numpy, OpenImageIO). |
| Poly Haven access | `assets/fetch_polyhaven.py` (stdlib only) caches assets in `assets/cache/` (gitignored). Every API call sends the User-Agent `vtf-visual-task-factory/0.0.1`. Models are fetched as glTF. | The API terms require a unique User-Agent naming the software. glTF avoids `.blend` files, which can carry embedded Python. The assets are CC0. If the Task Explorer ever pulls content from the live API, it must show a "Powered by Poly Haven" credit; our renders of downloaded assets need none. |

## 23 Nov 2026: Setup (stage 1)

| Decision | Choice | Why |
| --- | --- | --- |
| Blender version | **Blender 5.3.0**, installed with the Homebrew cask (`brew install --cask blender`) | Latest stable, released 10 Nov 2026. 5.2 is the LTS; we take 5.3 because the brief says latest stable and 5.3 adds RTX Spark support for the DGX Spark plan. Pin this version in Docker and CI later; images are only byte-identical on the same Blender version (Rule C). |
| GitHub account | Repo lives at `Zugzwang-world/hrishikesh-visual-task-factory`, public, default branch `main` | That is the account `gh` is logged in as on this Mac. |
| Commit email | This repo's git config uses the account's GitHub no-reply address | The GitHub account blocks pushes that expose its private email. Worktrees share the setting. |
| CI action pins | `actions/checkout@v7`, `astral-sh/setup-uv@v10.4.0` | setup-uv publishes only full version tags, so a bare `@v10` fails. |
| Python | 3.12, pinned through uv (`.python-version` and `requires-python`) | As the brief's stack table says. Blender ships its own Python; the `vtf` core never runs inside it. |
| Workspace layout | uv workspace with two members: `packages/vtf` (core, CLI) and `packages/vtf-blender` (scripts run by Blender) | Keeps `bpy` out of the core. The renderer talks to the core only through files. |
| CLI framework | Typer | The setup brief names it. |
| Lint | Ruff, configured in the root `pyproject.toml` | Fast, one tool; no gating beyond what CI runs. |
| CI | One GitHub Action: `uv sync` then `pytest` | Minimal proof that main runs. Determinism checks join it in stage 2. |
| Smoke render | Cycles on the Metal GPU, 64 samples, 640x480, beauty + object-index + depth passes | Proves the headless render path and the passes the gates need. Metal works; results are in OPEN.md. |
| Passes without the compositor | Render to a multilayer EXR and read the passes with Blender's bundled OpenImageIO and numpy | The compositor API changed in Blender 5.x; the EXR route keeps exact float depth and integer object index for G2 with no node setup. |
| No volatile metadata in outputs | Turn off every `use_stamp*` option and re-encode the beauty PNG without Cycles' render-time text chunks | So identical pixels give identical bytes; needed for G7. |
| Two Pythons | The workspace runs on Python 3.12; Blender 5.3 bundles Python 3.13, which runs everything in `packages/vtf-blender/scripts/` | Blender's Python can't be changed. Renderer scripts must stay compatible with Blender's Python and import nothing from `vtf`. |
| Subagent definitions | `.claude/agents/` with builder, expert, reviewer and scout, using the documented frontmatter keys (`model`, `effort`, `isolation`) | Checked against the Claude Code subagent docs on 23 Nov 2026; see RESEARCH.md. |

## 23 Nov 2026: Decided in BRIEF.md

| Decision | Choice |
| --- | --- |
| Image family | Robot workcell: a tabletop seen through robot cameras, rendered in 3D |
| Image source | Blender only; no AI image generators anywhere in the image path |
| Rendering | Mac first; DGX Spark or a cloud GPU when volume needs it |
| Model access | Own API keys |
| Working style | Prototype and flexible: no licence, compliance or production work; no test gates beyond the checks the judges score |
| Organizer contact | None for now; questions later through their channels |
| Repo | hrishikesh-visual-task-factory, public from day one |
| Team | Founder plus Claude Code; no other humans |
| Task Explorer | Built and hosted on Vercel |
| Pitch | Technical and explicit: beyond 2D, exact 3D truth, robot perception for physical AI |
| Fallback | Financial price charts, only if 3D blocks us |

## 23 Nov 2026: Stage 2 contracts (P03)

| Decision | Choice | Why |
| --- | --- | --- |
| Contract home | pydantic models in `packages/vtf/src/vtf/core/` (scene, render_manifest, task, program); JSON Schemas generated into `spec/` by `uv run python -m vtf.core.schemas`; a test fails when they go stale | One source of truth; lanes import the models, outsiders read the schemas. |
| World conventions | Metres, Blender's Z-up right-handed world, quaternions wxyz; table centred on the origin with its top at z = table.height; object pose = centre of its local bounding box, dimensions = full extents | Matches Blender, so the renderer needs no conversions. |
| Camera conventions | Scene file stores the Blender camera pose (looks down local -Z). The manifest's K and world-to-camera use the OpenCV convention (x right, y down, z forward; pixel origin at the image's top-left corner; horizontal sensor fit) | OpenCV is what downstream tools and robot stacks expect. Core computes the same matrices from the scene file, so validate can check the renderer against them. |
| Depth | depth.npy holds planar camera-frame z in metres, float32, 0.0 where nothing is hit | Planar depth is what front-to-back and G2 compare; the manifest states `depth_kind`. |
| Pass indices | Objects 1..999, table 1000, gripper 1001, background 0 | Leaves room for G2 to find the gripper and table in pixels later. |
| Gripper | The scene file stores both the gripper parameters and its derived world-frame parts (`build_gripper`) | The renderer just builds boxes and a cylinder; no duplicated geometry convention. |
| Physics | Settle parameters live in the scene file (`physics`) | They decide the settled poses, so they are part of the truth and of the hash. |
| Answer vocabulary | Colours, shapes, synonyms and the shared normaliser live in `vtf.core.vocab` for v0 | Programs and scorer must share one normaliser. These are domain words in core; they move to the workcell pack in stage 3. |
| Fixtures | `spec/examples/run_example/` is a full tiny run made by an analytic numpy ray caster (`vtf.core.raycast`), not Blender | Exact index, depth and amodal masks with no Blender, so every lane can build against the contracts; the ray caster also checks Blender's camera and poses. |
| CLI wiring | `vtf generate/render/validate/verify/eval` call lane modules lazily (`vtf.scene.generate.generate_run`, `vtf.render.bridge.render_run`, `vtf.validate.pipeline.validate_run`, `vtf.validate.verify.verify_run`, `vtf.eval.runner.eval_run`) | Lanes work in parallel without editing one shared file. |
| Eval model facts (scout, 23 Nov 2026) | `claude-sonnet-5-5` takes image input; send the image block before the text; structured JSON through `output_config.format`; temperature is not accepted (400) on this model | Record model ID and API version with each result; repeat runs can differ. |

## 23 Nov 2026: Stage 2 lanes (P04 to P08)

| Decision | Choice | Why |
| --- | --- | --- |
| Settle (expert, measured on Blender 5.3) | Primitive collision shapes (never convex hull), 90 frames at 24 fps, 40 substeps, 20 iterations, split impulse, Bullet deactivation on, friction 0.6, margin 0.5 mm; step frame by frame; copy the final matrices, remove the rigid-body world, re-apply the poses; the gripper is not simulated | Blender's 10 substeps let cylinders fall through the table; 40/20 settled 20 of 20 test seeds by frame 57 in about 0.07 s. Convex hulls were 10x slower and let spheres roll. Bullet is bit-identical run to run on one machine. |
| Data passes on the CPU | Beauty renders on the GPU; index and depth come from a separate 1-sample CPU render, and each amodal mask from a 1-sample CPU render with everything else hidden | GPU depth differs by about 6e-8 m in 1 or 2 pixels per frame; the CPU passes are exact, equal to the GPU index pixel for pixel, and cost about 1 s per camera. This keeps rule C's "masks and depth exact" true. |
| Depth and index encoding | The Z pass is planar camera depth (verified to 1e-6 m); background 1e10 becomes 0.0; index is uint16, never uint8 (table 1000, gripper 1001) | The smoke script's uint8 cast would have wrapped the reserved indices. |
| Settle gate in validate | A scene is rejected (stage settle) if any object is still moving at the end, left the table, drifted more than 3 cm, or rests more than 2 mm off its support | The truth must describe a scene at rest. |
| Render agreement evidence | validate checks the manifest's K and world-to-camera against `vtf.core.projection` (rejects on mismatch) and records per-object IoU between Blender's index pass and the analytic ray cast of the settled scene | An independent check that the image shows the scene file; a preview of G2. |
| G7 at validate time | Records sha256 of the scene and settled files, the canonical answer, and the decoded index and depth arrays; checks the render used this exact scene file and that the scene re-samples byte-identically from its seed. `vtf verify` re-renders and compares | Hashing decoded pixels, not PNG bytes, keeps the check independent of the PNG encoder. |
| Sampler footprints and spacing | Boxes use half their footprint diagonal; round shapes half their diameter; 1.5 cm gaps, 4 cm table margin | Holds at any yaw without spacing round objects needlessly. |
| Recipe hash | sha256 of the validated recipe with defaults filled in | Recipes that sample the same scenes hash the same; a change to a core default changes it. |
| Sampler draw order | One seeded stream in fixed order (camera, object count and pairs, each object, gripper, HDRI); bounded retries per object, then whole-layout restarts on the same stream | Determinism with rejection sampling. |
| Sampler ranges | Camera 0.88 to 0.92 m, tilt 38 to 42 degrees, azimuth within 5 degrees, focal 26 to 30 mm; gripper yaw -90 to 90 degrees; all corners at least 16 px inside the frame | Mild variety; everything in view. |
| Count questions say "on the table" | Both count phrasings name the table; counts with an object over the table but not resting on it are rejected (`ambiguous_support`) | The program counts objects resting on the table, so the wording must say so. |
| Nearest to the gripper | Measured from the tip point to each object's exact nearest surface; phrasings say "tip of the gripper" | Distance from the whole gripper would be a different question. |
| Front-to-back | Planar depth decides, and the question is rejected unless straight-line distance agrees with a 4 cm margin too | The answer must not depend on which meaning of "closer" a reader assumes. |
| Eval adapter | Official `anthropic` SDK 2.12.0 (API version 2026-10-21); thinking adaptive and effort high pinned in configs/models.yaml and recorded; no temperature; structured JSON output with a plain-text fallback; SDK retries; key read only from ANTHROPIC_API_KEY | Repeatable settings on record; no silent billing to another credential. |
| Refusals and parse failures | A refusal is scored as the model's (unparseable, wrong); unparseable replies count wrong and are tracked; API failures are not cached | Honest scoring; reruns retry real failures. |
| Prompts | One system prompt per answer type, a `<colour> <shape>` placeholder plus the vocabulary list, no concrete example answer | Avoids anchoring the model on an example. |
| Floor depth (render review) | Floor and table legs are renderer props outside the scene file: index 0, but depth.npy keeps their depth; 0.0 only where nothing is hit | Honest depth of the image; no gate compares full-frame depth with the ray cast. |
| Re-render rules (render review) | `vtf render` skips a scene only when its manifest was rendered from the current scene file (sha256); a failed render never leaves a manifest; the renderer refuses an already-settled scene; settle_frames >= 2 | Stale or half-written renders must never pass as truth. |
