# OPEN

Open questions, each with a recommendation. Close an item by moving its answer to DECISIONS.md.

| # | Question | Recommendation | Status |
| --- | --- | --- | --- |
| 1 | Which exact model IDs do we evaluate? | Claude models only (founder, 25 Nov): claude-haiku-5-5, claude-sonnet-5-5, claude-opus-5-5 (high and xhigh); never Fable or Mythos; others only if judges ask. | Closed 25 Nov (DECISIONS, brief VTF-07) |
| 2 | How fast does Blender render on this Mac (Apple M4, 10-core GPU)? Is it fast enough for about 800 scenes x 3 cameras? | **Re-measured 23 Nov 2026 on the stage-2 workcell scene** (1024x768, 128 samples, denoiser, HDRI, 5 to 10 primitives, gripper, rigid-body settle, CPU data passes and amodal masks). Builder, quiet machine: 9.7 to 14.3 s wall clock per scene including Blender start-up (beauty 8.6 to 12.4 s; settle 0.05 to 0.08 s; index and depth 0.1 to 0.3 s; amodal masks 0.2 to 0.5 s). P09 run of 20 scenes with the machine heavily loaded (load average about 30): 21 s per scene, 7 minutes in all. So about 11 to 20 s per camera: 800 scenes x 3 cameras is about 7.5 to 13 hours, an overnight run, well under the 30 s threshold. If volume needs it, 64 samples with the denoiser's FAST pre-filter roughly halves beauty time; batching several scenes per Blender process saves about 1.5 s each. | **Re-measured for stage 3 (23 Nov, night):** v1 scenes (3 to 25 Poly Haven objects, three cameras at 1024x768, 64 samples plus OIDN) take about 15 to 20 s to render per scene in batched Blender, plus G6 twins; the scale run averages about 25 s per scene all in, so 500 scenes take about 4 hours. Answered |
| 3 | Does Metal GPU rendering work headless in Blender on this Mac? | **Yes.** Cycles logs "Path tracing on: Apple M4 (GPU - 10 cores) (Metal)", and the GPU is 5x faster than the CPU at 1024 samples. No CPU fallback needed. | Answered |
| 6 | GPU renders are not bit-exact run to run (2 of 307,200 beauty pixels differ by one colour level). | **Closed 23 Nov 2026.** G7 checks scene files, answers, masks and depth exactly; images pass at SSIM 0.999 or above; GPU default, CPU option for byte-identical images. See DECISIONS.md. | Closed |
| 4 | Expected task count and required package format from the organizers. | Raise later through their channels (brief Part 4). Keep our versioned spec plus a converter. | Open |
| 5 | Real objects for physical twins: buy or borrow? | Buy the trimmed list: 6 kinds, 8 pieces for the 12 layouts (docs/twins/shopping_list.md, DEMO.md); print docs/twins/mat_A3.pdf at 100%. | Waiting on the founder |
| 7 | The asset manifest is not part of any hash, so editing it changes scenes without changing the recipe hash that G7 checks. | Add asset_manifest_sha256 to scene meta (or run.json) in contract v0.3 and include it in G7. | Open (stage 4) |
| 8 | Lay-flat rest poses are carried by pre-rotated glTF files and free-text notes. | A structured rest_rotation field on AssetEntry in contract v0.3. | Open, low priority |
| 9 | Some families yield few tasks per scene: occlusion (strict rules), gripper_view (needs one object under a 15 px disc), relation (5-degree cone edge). | Tune after the founder audit, not before; the dataset report shows the yield per family. | Open |
| 10 | G6 twin rules are weak for front_to_back, nearest, occlusion and gripper_view (often no valid move). | Untwinned tasks record G6 as no_twin_rule, not a failure; improve the rules in stage 4 if the judges' demo needs twins in those families. | Open |
| 12 | Three G6 twins failed with "ProgramReject got multiple values for argument 'reason'" (a twin rule passes reason= as a detail keyword). | Rename the detail key in vtf.programs.twins; the twins were recorded twin_invalid and did not affect their originals. | Open, small fix |
| 13 | The scale run stopped at 300 of 500 scenes (disk floor). | v1 stays canonical at 300 scenes; the last 200 are not rendered. | Closed 25 Nov (founder decision, VTF-07) |
| 14 | Scenes 0130, 0174 and 0229 fail validation: "zero-size array to reduction operation maximum". | **Fixed 24 Nov (brief 06) on branch claude/phase-e-swap-crash-06, merged.** The crash was not in vtf.programs.view as guessed but in G2's pixel recomputation: support (r_support) back-projects the ring of pixels around an object, and on a camera with lens distortion (mild, harsh, cctv) an empty pixel set reached vtf.camera.dial._undistort_normalised, which took the max of an empty array. The function now returns nothing for no points (test added). All 300 scenes re-validated into out/v1_1_check (no twins, nothing rendered): 264 scenes validate (261 + the 3), 3,511 base tasks (3,475 + 36); the other 261 scenes give identical task ids and answers; new tasks only in 0130 (11), 0174 (11) and 0229 (14). Diff: docs/reports/v1/v1_1_check_diff.json. v1 unchanged. | Closed 25 Nov: no v1.1 release (founder decision); the diff stays as repeatability evidence. |
| 15 | Swap, not our output, decides free disk on this Mac: 14 to 21 GB of swap moves free space by several GB within minutes. | **Measured 24 Nov 01:32 (brief 06).** One development batch of 5 scenes (recipe v1, seed 606, out/dev_swap_06) in one Blender process; memory by process family every 3 s (scripts/mem_sampler.py, out/dev_swap_06/memlog.csv) and per scene inside Blender (a VTF_MEMSTAT line in each render.log). Blender does not leak between scenes: resident size 1.28, 1.33, 1.19, 1.13 and 1.14 GB after scenes 1 to 5 (peak 1.9 GB in the first); its physical footprint (includes Metal GPU memory) peaks at 6.6 GB during every render and falls below 1 GB between scenes. The asset cache keeps meshes, materials and images between scenes by design (images 39 to 78 datablocks), but only about 2 megapixels stay loaded and the cache is bounded by the 40-asset library. Validation peaks at 137 MB. With the usual apps open (Claude app 2.0 GB, Chrome 4.0 GB, 7.8 GB of other processes) each render leaves 150 to 400 MB free on this 24 GB Mac, so anything more (Docker Desktop's VM, more browser tabs, G6 twin renders) pushes macOS into swap, and swap files only shrink after a restart. | Not a leak, so no purge fix. Before a long run: restart, quit Docker Desktop and Chrome, keep the 20 GB disk floor. | Answered: memory pressure during renders |
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
