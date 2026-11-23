# OPEN

Open questions, each with a recommendation. Close an item by moving its answer to DECISIONS.md.

| # | Question | Recommendation | Status |
| --- | --- | --- | --- |
| 1 | Which exact model IDs do we evaluate (three closed flagships from Anthropic, Google and OpenAI, plus two open models such as a Qwen VL model)? | Verify on the web on the day the evaluation stage starts (Vision Arena leaderboard plus each provider's model list), pin the IDs in `configs/models.yaml`, and record them here. Until then every entry is marked unverified. | Open until stage 4 |
| 2 | How fast does Blender render on this Mac (Apple M4, 10-core GPU)? Is it fast enough for about 800 scenes x 3 cameras? | **Re-measured 23 Nov 2026 on the stage-2 workcell scene** (1024x768, 128 samples, denoiser, HDRI, 5 to 10 primitives, gripper, rigid-body settle, CPU data passes and amodal masks). Builder, quiet machine: 9.7 to 14.3 s wall clock per scene including Blender start-up (beauty 8.6 to 12.4 s; settle 0.05 to 0.08 s; index and depth 0.1 to 0.3 s; amodal masks 0.2 to 0.5 s). P09 run of 20 scenes with the machine heavily loaded (load average about 30): 21 s per scene, 7 minutes in all. So about 11 to 20 s per camera: 800 scenes x 3 cameras is about 7.5 to 13 hours, an overnight run, well under the 30 s threshold. If volume needs it, 64 samples with the denoiser's FAST pre-filter roughly halves beauty time; batching several scenes per Blender process saves about 1.5 s each. | **Re-measured for stage 3 (23 Nov, night):** v1 scenes (3 to 25 Poly Haven objects, three cameras at 1024x768, 64 samples plus OIDN) take about 15 to 20 s to render per scene in batched Blender, plus G6 twins; the scale run averages about 25 s per scene all in, so 500 scenes take about 4 hours. Answered |
| 3 | Does Metal GPU rendering work headless in Blender on this Mac? | **Yes.** Cycles logs "Path tracing on: Apple M4 (GPU - 10 cores) (Metal)", and the GPU is 5x faster than the CPU at 1024 samples. No CPU fallback needed. | Answered |
| 6 | GPU renders are not bit-exact run to run (2 of 307,200 beauty pixels differ by one colour level). | **Closed 23 Nov 2026.** G7 checks scene files, answers, masks and depth exactly; images pass at SSIM 0.999 or above; GPU default, CPU option for byte-identical images. See DECISIONS.md. | Closed |
| 4 | Expected task count and required package format from the organizers. | Raise later through their channels (brief Part 4). Keep our versioned spec plus a converter. | Open |
| 5 | Real objects for physical twins: buy or borrow? | Buy: the 15-item, 23-piece supermarket list in DEMO.md and docs/twins/shopping_list.md, all matched to assets; print docs/twins/mat_A3.pdf at 100%. | Waiting on the founder |
| 7 | The asset manifest is not part of any hash, so editing it changes scenes without changing the recipe hash that G7 checks. | Add asset_manifest_sha256 to scene meta (or run.json) in contract v0.3 and include it in G7. | Open (stage 4) |
| 8 | Lay-flat rest poses are carried by pre-rotated glTF files and free-text notes. | A structured rest_rotation field on AssetEntry in contract v0.3. | Open, low priority |
| 9 | Some families yield few tasks per scene: occlusion (strict rules), gripper_view (needs one object under a 15 px disc), relation (5-degree cone edge). | Tune after the founder audit, not before; the dataset report shows the yield per family. | Open |
| 10 | G6 twin rules are weak for front_to_back, nearest, occlusion and gripper_view (often no valid move). | Untwinned tasks record G6 as no_twin_rule, not a failure; improve the rules in stage 4 if the judges' demo needs twins in those families. | Open |
| 12 | Three G6 twins failed with "ProgramReject got multiple values for argument 'reason'" (a twin rule passes reason= as a detail keyword). | Rename the detail key in vtf.programs.twins; the twins were recorded twin_invalid and did not affect their originals. | Open, small fix |
| 13 | The scale run stopped at 300 of 500 scenes (disk floor). | Free about 3 GB (Docker Desktop's VM disk is 41 GB), then resume with the command in NOW.md; rerun balance, eval and report. 3,181 tasks already meet the brief's target of about 3,000. | Founder |
| 11 | RejectedRow has no task_id field; G5 rebalance rows carry it in detail.task_id. | Add an optional task_id in contract v0.3. | Open, low priority |


## Smoke render results (23 Nov 2026)

Script: `packages/vtf-blender/scripts/smoke_render.py`. Scene: ground plane, cube, sphere and cylinder, one sun, Cycles, 640x480, fixed seed 0, denoiser off. Blender 5.3.0 on an Apple M4 (10-core GPU), macOS 15.

| Measure | Result |
| --- | --- |
| GPU used? | Yes, Metal. Cycles log: "Path tracing on: Apple M4 (GPU - 10 cores) (Metal)" |
| First-ever GPU render | About 230 s wall clock: a one-time Metal kernel compile, cached afterwards. Expect the same once on any new machine or Blender version. |
| Warm render, 64 samples | 0.8 to 1.7 s render time; about 2 s wall clock including Blender start-up |
| 1024 samples, GPU vs CPU | 6.3 s vs 32.8 s (GPU 5.2x faster) |
| Passes | Beauty PNG, multilayer EXR (Combined, Depth in metres, Object Index), plus PNG exports of the index and depth. Object-index pixel counts: cube 6,038, sphere 6,461, cylinder 7,409, ground 202,554, background 84,738. |
| Determinism | See item 6 above |

Implementation notes for stage 2:

- Blender 5.3 writes multilayer EXR as multi-part (one subimage per pass) and names the pass `Object Index`, not `IndexOB`. Read it with OpenImageIO's `ImageBuf(path, subimage, 0)`; numpy and OpenImageIO ship inside Blender.
- In Blender 5.x, set `image_settings.media_type = "MULTI_LAYER_IMAGE"` before `file_format = "OPEN_EXR_MULTILAYER"`.
- The smoke script avoids the compositor entirely, because the compositor API changed in 5.x.
