# Validation

Every task in dataset v1 passed the same model-free checks, and every rejected question is logged with its reason. No AI model writes or checks an answer: answers come from typed programs run on the settled scene file, and models appear only in G5, as a filter. All numbers below are read from files in the repo or in the release ([out/v1/validate_report.json](https://huggingface.co/datasets/Hrishi1802/hrishikesh-visual-task-factory/blob/main/validate_report.json), [docs/reports/v1/report_data.json](reports/v1/report_data.json), [docs/reports/v1/FROZEN.json](reports/v1/FROZEN.json)); the dataset report ([docs/reports/v1/index.html](reports/v1/index.html)) draws them as charts.

## Three design rules

- **A. Answerable from what is visible.** Centimetre questions (distance, free space) are asked only when the printed cm grid or a procedural scale reference is in the task's camera. Every object a question names must show at least 40% of its silhouette and 300 pixels; counted objects at least 15% and 120 pixels, so nothing counted is fully hidden. Every object a question names must have a unique name in the scene (category, then colour, size, size and colour). Enforced when questions are compiled and by G3. Thresholds: [spec/families.md](../spec/families.md).
  - **Scale stated (closed 25 Nov, brief VTF-08).** The founder found in the audit that v1's distance and free-space questions never say what the scale is. From programs 0.3.0 those questions open with one sentence: "The table has a printed grid of 1 cm squares with bold lines every 10 cm.", or, on the 53% of such tasks with no grid, the reference object's exact size ("The cube is exactly 5 cm on each side."), since the grid sentence would be false there. Frozen v1 is unchanged; its 272 distance and free-space eval and audit tasks were re-asked to all four Claude settings with the sentence (eval condition "scale stated", results kept apart in out/v1/sweep_scale/). Stating it lifts Sonnet 5.5 by 10 points (+4 to +15) and Haiku 5.5 by 5 (-1 to +11), almost all on distance; Opus 5.5 at high and xhigh does not move (+1). The audit comparison uses the scale-stated answers. [reports/v1/scale_stated.md](reports/v1/scale_stated.md).
- **B. No near-ties.** Compared distances, depths and positions must differ by both a metric and a pixel margin (for example 3 cm and 10 px for nearest to the gripper, 5 cm on the front camera). Enforced by G4; the margin table is in [spec/families.md](../spec/families.md#g4-summary).
- **C. Honest repeatability.** Exact on every re-render, GPU or CPU: scene files and settled scenes (byte-identical), answers and G7 hashes, object index, depth and amodal masks (equal arrays). These carry all the truth, and G2 recomputes answers from them. Near-exact: beauty images, because path tracing and the denoiser move a few colour levels between runs. The brief's targets were SSIM 0.999 on the GPU and byte-identical CPU images; measured, GPU re-renders reach SSIM 0.998 to 0.9999 and two CPU renders differ by a few colour levels in under 0.01% of pixels. Enforced by G7 and `vtf verify`; measured in [reports/v1/verify_showcase.md](reports/v1/verify_showcase.md).

## The gates

| Gate | What it proves | How | v1: passed / run |
| --- | --- | --- | --- |
| G1 Truth by execution | The answer is right | The typed program runs again on the settled scene file and must give the identical answer; program, trace and answer stored | 3,653 / 3,653 (100%) |
| G2 Second computation | The answer survives an independent route | Recomputed from the rendered object-index and depth pixels (back-projected through the camera and the lens dial's exact inverse), not from scene coordinates; both must agree | 3,486 / 3,653 (95.4%) |
| G3 Evidence visible | The image holds the evidence | Rule A thresholds on every evidence object, from the rendered masks | 3,633 / 3,653 (99.5%) |
| G4 No near-ties | Exactly one right answer | Rule B margins, metric and pixel | 3,649 / 3,653 (99.9%) |
| G5 Vision necessity | The answer cannot be guessed without the image | Answer distributions capped per family (yes/no 45 to 55%, any one answer at most 35%), then prior-only and question-only baselines | 3,181 of 3,959 kept (778 dropped to balance) |
| G6 Counterfactual twin | The answer depends on the scene | Seeded 20% sample per family and camera rig: edit the scene by the family's twin rule (move, remove, flip), re-render, re-run G1 to G4; the answer must change | 484 / 484 valid twins changed the answer |
| G7 Determinism | Same seed, same task | Hashes of scene, settled scene, index, depth and answer; scene re-sampled from the seed and compared byte for byte | 3,653 / 3,653 (100%) |
| G8 Physical twin | A real photo still shows the evidence | Real objects placed on a printed mat by a generated layout; ArUco corners map the layout onto the photo | Proved on digital twins (36 of 36 pass, 3 of 3 misplaced rejected); real photos to come, see below |

## The v1 funnel

From [docs/reports/v1/FROZEN.json](reports/v1/FROZEN.json) and validate_report.json (recipe `recipes/v1.yaml`, seed 1, code c3e8ae2):

| Step | Count | What was dropped |
| --- | --- | --- |
| Scenes generated | 500 | Frozen at the first 300: the run stopped itself at the 20 GB free-disk floor |
| Scenes rendered | 300 | |
| Scenes validated | 261 | 36 rejected by the settle check (an object moved more than 3 cm on the table, or 10 cm inside a container, in the physics settle); 3 crashed in validation (fixed later, see below) |
| Candidate questions | 7,013 | Every question the programs drafted for 261 scenes x 3 cameras |
| Reached the gates | 3,653 | 3,360 rejected while compiling: object not occluded (669), no candidate (549), evidence hidden (351), occluder rests on the answer (245), near tie in depth (243), zone not in frame (197), unnameable object (171), near tie to the gripper (158), and 13 smaller reasons |
| Passed G1 to G4 and G7 | 3,475 | 178 rejected: G2 disagreed (167), G3 (8), G4 (3) |
| With G6 twin tasks | 3,959 | +484 twin tasks whose answer changed |
| After G5 balance (frozen v1) | **3,181** | 778 dropped to flatten answer distributions (count 364, support 301, count by colour 72, containment 41) |

So 3,181 of 7,013 candidate questions shipped (45%), and every one of the 4,352 drops is logged: rejected.jsonl (3,574 rows, one per question or scene) and baselines/balance.json (778). Shipped tasks: 12 families, 3 cameras (front 1,253, overhead 1,235, gripper 693), 424 spatial tasks where the 2D shortcut conflicts with the 3D truth, 1,071 where it agrees, 1,686 non-spatial. Frozen sha256: manifest.jsonl `70843c15...272b`, rejected.jsonl `a969be12...d046`.

## G5 baselines

From out/v1/baselines/ (prior.json, blind.json):

| Baseline | Accuracy |
| --- | --- |
| Prior only (always the family's most common answer), before balance | 39.5% |
| Prior only, after balance, all 3,181 tasks | 24.7% |
| Claude Sonnet 5.5 without the image (question only, 15 tasks per family, 180 tasks) | 15.0% (95% CI 10.0 to 20.6) |
| Claude Sonnet 5.5 with the image (1,500-task stratified sample) | 61.9% (95% CI 59.5 to 64.3) |

The question-only model beats the prior in no family (its interval never clears the prior). Without the image it guesses objects that are not on the table (76 of 180 answers name things like a cup or a banana), so object questions cannot be answered from the text. Balancing cut the prior from 39.5% to 24.7%. With the image, the four Claude settings we tested score 50.3% (Haiku 5.5 high) to 66.4% (Opus 5.5 xhigh) on the same 1,567 tasks ([findings](reports/v1/findings.md)).

## G6 counterfactual twins

872 accepted tasks were sampled (20% per family x camera, seeded by task ID). 299 had no twin rule for that program, 89 twins were invalid (78 failed the settle check, 6 failed G2, 3 hit an error, 2 were near ties) and were not counted against the question, and 484 valid twins all changed the answer, each re-rendered and re-validated through G1 to G4. Those 484 twin tasks ship in v1 next to their originals.

## G7 determinism and the three-scene fix

G7 records hashes at validation time; `vtf verify` reproduces them. Three frozen v1 scenes (39 tasks) re-rendered once on the GPU and twice on the CPU for [reports/v1/verify_showcase.md](reports/v1/verify_showcase.md): every scene file re-sampled byte-identical from the seed, every settled scene, answer and G7 hash identical, every mask and depth array exact. A fresh clone of the repo running `vtf quickstart` reproduced the quickstart run byte for byte in everything but the images (7 scene files, 21 index and depth arrays, 112 task IDs and answers, the rejection log; [reports/quickstart_check.md](reports/quickstart_check.md)).

The same check found a renderer bug. A scene rendered alone does not reproduce its frozen v1 image (SSIM 0.84 to 0.99), while the same scene rendered after the scenes that preceded it in the frozen run's Blender process does (SSIM 0.9986 or above). The cause: after each render, the 3D-viewer export (scene.glb) swaps the materials of the scene's objects for simplified ones and shrinks their textures, in place, on asset meshes that the Blender process keeps for later scenes. So in v1, an object already drawn by an earlier scene in the same process shows the simplified material (its base-colour texture at 256 px, no normal, roughness or metal maps), which can affect 248 of 261 scenes. Geometry, masks, depth and every answer are unaffected, since they come from the scene file and the data passes. v1 stays as frozen; the fix (export from copies) belongs in the renderer before the next render run.

A second piece of repeatability evidence came from a bug. Three scenes (0130, 0174, 0229) crashed in validation on an empty point set in the lens undistort; the fix (a guard in `vtf.camera.dial`) touched only that crash path. All 300 scenes were then re-validated on the fixed code into a separate folder ([docs/reports/v1/v1_1_check_diff.json](reports/v1/v1_1_check_diff.json)): the other 261 scenes gave identical tasks and answers, 0 answers changed, and the only difference was 36 new tasks in the three fixed scenes (11, 11 and 14). **v1 stays canonical**: it is not re-released with those 36 tasks; the check stands as evidence that a code fix changes only what it should.

## Founder blind audit

> **Placeholder.** 150 tasks (stratified by family, camera and shortcut conflict; 46 conflict tasks) answered blind by the founder on a labelling page with no answers in it, scored with the eval scorer, target 98%. The draw that replaced tasks from scenes already shown with answers in the Task Explorer is docs/reports/v1/audit_v2_sample.json. Result and every miss traced to its cause: to be added.

## Physical twins (G8)

12 generated layouts built from 8 real objects (6 kinds) on the printed mat ([docs/twins/](twins/)), chosen to cover every question family with the fewest objects ([selection.json](twins/selection.json)), photographed from three phone spots ([photo_session.md](twins/photo_session.md)). G8 finds the mat's ArUco corner markers, works out the phone's pose, and checks every object against its layout position; each passing photo gets a synthetic sibling rendered from that estimated pose, so real and rendered images carry the same exact questions.

**Proved on the digital twins before any photo** ([g8_digital_check.md](twins/g8_digital_check.md)), the 12 layouts rendered on the printed mat from the three spots:

- Correctly placed: **36 of 36 views pass**. Misplaced control (one object moved 4 cm): **3 of 3 views rejected** (measured 39 to 41 mm).
- Camera pose from the markers: median error 0.62 mm and 0.055 degrees (max 3.2 mm, 0.30 degrees); marker reprojection at most 1.4 px.
- Placement of correctly placed objects: median error 1 mm, max 10 mm, against a 2 cm tolerance. White objects (tape roll, spray bottle) on the white mat are not checked automatically; their outlines go on an overlay for a glance.
- End to end on 39 stand-in photos (renders under shuffled names): every correct view recognised and passed, 154 tasks paired one to one with their rendered siblings ([photo_pipeline_proof.md](twins/photo_pipeline_proof.md)). One layout (L08) gives no tasks: its bowl contents never settle in the physics simulation, so the photo guide says to skip it.

**Hero layouts for the trap** ([twins/trap_check.md](twins/trap_check.md)). The 12 layouts give only 8 conflict tasks among their 154 (1 on the front spot), and Sonnet 5.5 and Opus 5.5 both took the 2D shortcut on just one. Three hero layouts (H1 to H3, five objects each, front spot, shot first) were chosen from 103 sampled and 20 rendered candidates because both models take the shortcut on their digital twins; their trap questions are pinned so every photo asks them. H1 is the one to film: "Is there room to place the tape on the table between the bottle and the lime?" The gap is 6.9 cm and the tape 4.5 cm, so the answer is yes; both models say no.

> **Placeholder.** Real photos, their G8 results and the rendered-to-real accuracy gap per Claude setting (`uv run vtf twin sim-to-real`): to be added after the photo session.
