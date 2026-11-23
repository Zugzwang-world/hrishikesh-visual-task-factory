# OPEN

Open questions, each with a recommendation. Close an item by moving its answer to DECISIONS.md.

| # | Question | Recommendation | Status |
| --- | --- | --- | --- |
| 1 | Which exact model IDs do we evaluate (three closed flagships from Anthropic, Google and OpenAI, plus two open models such as a Qwen VL model)? | Verify on the web on the day the evaluation stage starts (Vision Arena leaderboard plus each provider's model list), pin the IDs in `configs/models.yaml`, and record them here. Until then every entry is marked unverified. | Open until stage 4 |
| 2 | How fast does Blender render on this Mac (Apple M4, 10-core GPU)? Is it fast enough for about 800 scenes x 3 cameras? | Simple scene is about 1 s per image on the GPU (see below). One Poly Haven bowl with a 1k HDRI, 800x600, 128 samples plus denoising: 7.9 s warm. Re-measure in stage 2 on a real workcell scene with Poly Haven assets and HDRI lighting, which is far heavier. If a full scene takes more than about 30 s per camera, drop samples and use the denoiser, render overnight, or move volume to the DGX Spark or a cloud GPU. | Partly answered; re-measure in stage 2 |
| 3 | Does Metal GPU rendering work headless in Blender on this Mac? | **Yes.** Cycles logs "Path tracing on: Apple M4 (GPU - 10 cores) (Metal)", and the GPU is 5x faster than the CPU at 1024 samples. No CPU fallback needed. | Answered |
| 6 | GPU renders are not bit-exact run to run (2 of 307,200 beauty pixels differ by one colour level). | **Closed 23 Nov 2026.** G7 checks scene files, answers, masks and depth exactly; images pass at SSIM 0.999 or above; GPU default, CPU option for byte-identical images. See DECISIONS.md. | Closed |
| 4 | Expected task count and required package format from the organizers. | Raise later through their channels (brief Part 4). Keep our versioned spec plus a converter. | Open |
| 5 | Real objects for physical twins: buy or borrow? | Common kitchen items that match Poly Haven assets. | Open |

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
