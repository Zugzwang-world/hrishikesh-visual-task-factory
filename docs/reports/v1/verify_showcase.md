# vtf verify on frozen v1: what comes back exact, and what does not

Rule C says: scene files and answers byte-identical, masks and depth exact on every re-render, images near-exact. This report measures it on frozen dataset v1 with one command, writing nothing into the frozen run:

```bash
uv run vtf verify-showcase out/v1      # writes out/verify_showcase/ (showcase.json, verify_showcase.md)
```

On a fresh machine, get `out/v1` from the Hugging Face release first (`hf download Hrishi1802/hrishikesh-visual-task-factory --repo-type dataset --local-dir out/v1`) and the 40 CC0 models with `uv run python assets/fetch_manifest.py`. Measured on an Apple M4 (10 cores, 24 GB), macOS 15.7.3, Blender 5.3.0, Metal GPU, on 25 Nov 2026, with the code of this branch (the renderer differs from the freeze at c3e8ae2 only by a memory log line).

**Scenes.** scene_0004, scene_0170 and scene_0014, picked to cover the three clutter levels (5, 16 and 19 objects), three lighting conditions (dim, normal, coloured) and all four camera-quality levels (clean, mild, harsh, CCTV); 13 tasks each, 39 in all.

**What it does.** Three passes of `vtf verify`, each scene rendered alone in a fresh Blender process: once on the GPU (as v1 was rendered) and twice on the CPU. Each pass re-samples the scene file from `recipes/v1.yaml` and the scene's seed, re-renders it (physics settle, beauty, object index, depth and amodal masks, the camera-quality dial), re-runs every task's program on the new settled scene, and compares everything with the frozen files. The two CPU passes are then compared with each other. Finally it replays the frozen run's first render batch: scenes 0000 to 0004 in one Blender process with the scene.glb export after each, as the scale run rendered them.

## Result

- **Exact, in all three passes:** 3 scene files re-sampled byte-identical, settled scenes byte-identical, object index, depth and amodal masks equal on all 9 camera images, all 39 answers and every G7 hash identical.
- **CPU twice:** 6 of 9 beauty PNGs byte-identical; the rest within a few colour levels in under 0.01% of pixels.
- **Rendered alone vs frozen:** SSIM 0.838 to 0.990 on GPU and CPU alike (a renderer bug, below).
- **Frozen batch replayed on the GPU:** SSIM 0.9986 to 0.9999 against the frozen images, 8 of 15 at the 0.999 target; masks, depth and settled scenes exact.

### Per camera image

| Scene | Camera | Tasks | Scene file | Settled | Masks + depth | Answers | G7 hashes | SSIM alone GPU vs frozen | SSIM alone CPU vs frozen | CPU vs CPU PNG | CPU vs CPU max diff (pixels differing) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| scene_0004 | front | 13 | yes | yes | yes | yes | yes | 0.978817 | 0.978930 | identical | 0 (0.0000%) |
| scene_0004 | overhead | 13 | yes | yes | yes | yes | yes | 0.987848 | 0.987959 | differs | 1 (0.0019%) |
| scene_0004 | gripper | 13 | yes | yes | yes | yes | yes | 0.838273 | 0.838290 | differs | 1 (0.0006%) |
| scene_0170 | front | 13 | yes | yes | yes | yes | yes | 0.983943 | 0.984219 | differs | 4 (0.0073%) |
| scene_0170 | overhead | 13 | yes | yes | yes | yes | yes | 0.982989 | 0.983461 | identical | 0 (0.0000%) |
| scene_0170 | gripper | 13 | yes | yes | yes | yes | yes | 0.990042 | 0.989923 | identical | 0 (0.0000%) |
| scene_0014 | front | 13 | yes | yes | yes | yes | yes | 0.976154 | 0.976596 | identical | 0 (0.0000%) |
| scene_0014 | overhead | 13 | yes | yes | yes | yes | yes | 0.978891 | 0.979065 | identical | 0 (0.0000%) |
| scene_0014 | gripper | 13 | yes | yes | yes | yes | yes | 0.981382 | 0.981150 | identical | 0 (0.0000%) |

| Batch replay (GPU) | Camera | SSIM vs frozen | Max diff | Pixels differing | Masks + depth | Settled |
| --- | --- | --- | --- | --- | --- | --- |
| scene_0000 | front | 0.999881 | 1 | 3.52% | yes | yes |
| scene_0000 | overhead | 0.998899 | 9 | 3.14% | yes | yes |
| scene_0000 | gripper | 0.999917 | 1 | 2.99% | yes | yes |
| scene_0001 | front | 0.999926 | 1 | 2.80% | yes | yes |
| scene_0001 | overhead | 0.998895 | 10 | 11.37% | yes | yes |
| scene_0001 | gripper | 0.999883 | 1 | 3.14% | yes | yes |
| scene_0002 | front | 0.999082 | 7 | 8.68% | yes | yes |
| scene_0002 | overhead | 0.998838 | 7 | 9.45% | yes | yes |
| scene_0002 | gripper | 0.999923 | 1 | 2.56% | yes | yes |
| scene_0003 | front | 0.998913 | 9 | 9.17% | yes | yes |
| scene_0003 | overhead | 0.998588 | 8 | 4.43% | yes | yes |
| scene_0003 | gripper | 0.998646 | 11 | 12.66% | yes | yes |
| scene_0004 | front | 0.998614 | 8 | 3.98% | yes | yes |
| scene_0004 | overhead | 0.999908 | 1 | 2.86% | yes | yes |
| scene_0004 | gripper | 0.999907 | 1 | 3.05% | yes | yes |

## What the numbers say

1. **The truth is exact.** In every pass, on the GPU and the CPU, the scene files re-sample byte-identical from their seeds, the settled scenes (the physics result every answer is computed from) are byte-identical, the object index, depth and amodal masks equal the frozen arrays, all 39 answers come back identical and every G7 hash matches. In the batch replay too, every settled scene, mask and depth array is exact.
2. **CPU images are near-exact, not byte-exact.** Two CPU renders of the same scene differ in the raw render by at most one colour level in 0 to 0.003% of pixels (at most about 25 of 786,432); after the camera dial 6 of 9 PNG files are byte-identical and the other 3 differ by at most 4 colour levels in at most 0.0073% of pixels (SSIM 0.999998 or above). Which images match changes from run to run: the first run of this check had 7 of 9 identical. The brief promised byte-identical CPU images; on these v1 scenes that does not hold, and this is the honest number.
3. **A scene rendered alone does not reproduce its frozen image.** SSIM against the frozen images is 0.838 to 0.990, on the GPU and the CPU alike, while a fresh GPU and a fresh CPU render of the same scene agree at SSIM 0.9994 to 0.9997 (raw renders). So the frozen images differ from both, for a reason other than the device.
4. **Replaying the frozen batch reproduces them.** Rendering scenes 0000 to 0004 in one Blender process, as the frozen run did, matches the frozen images at SSIM 0.9986 to 0.9999 (largest difference 11 colour levels): scene_0004's gripper image goes from SSIM 0.838 alone to 0.9999 in the replay. That is GPU repeatability for v1 images: near-exact, slightly below the 0.999 target for some images.

## The renderer bug this found

The difference between "alone" and "in a batch" is a bug in the renderer, found by this check. The scale run renders a batch of scenes in one Blender process, and the process keeps each imported asset mesh for later scenes (`vtf_blender.build.AssetLibrary`). After each render, the export of the Task Explorer model (`vtf_blender.export.export_glb`) replaces the objects' materials with simplified ones (`_flatten_materials`) and shrinks their textures to 256 px (`_shrink`). Both act in place: the material slots belong to the cached mesh, and the texture images are shared. Every later scene in that process draws those assets with the simplified material: base-colour texture at 256 px, no normal, roughness or metal maps. In scene_0004 a brass pot, first drawn by scene_0002, comes out matte; in scene_0170 a wooden box loses its printed label.

- **What it affects:** the look of objects that an earlier scene in the same Blender process had drawn. 248 of the 261 v1 scenes were not the first scene of their process (their render logs give the position), so any of them can show it. The 484 G6 twin scenes were rendered without the export, so they do not have it; a twin image can therefore differ from its original in material detail as well as in the one edited object.
- **What it does not affect:** geometry, physics, masks, depth and every answer. They come from the scene file and the data passes, which never use these materials, so every gate result stands. Object colours stay close, because the simplified material keeps the base-colour texture.
- **What to do:** v1 stays as frozen. Before the next render run, export from copies of the materials and images (or reload the assets per scene) so an image depends only on its scene file; then `vtf verify` of a single scene reproduces batch-rendered images too. The quickstart does not export scene.glb, so its renders do not have the bug.

## Timings

| Pass | Wall time (s) | Render time per scene (s) |
| --- | --- | --- |
| gpu | 50.2 | scene_0004 13.35, scene_0014 16.67, scene_0170 15.14 |
| cpu_1 | 141.8 | scene_0004 44.36, scene_0014 48.99, scene_0170 46.59 |
| cpu_2 | 156.3 | scene_0004 45.25, scene_0014 56.65, scene_0170 52.61 |
| batch replay | 62.0 | scene_0000 11.4, scene_0001 12.9, scene_0002 10.3, scene_0003 12.9, scene_0004 12.1 |

The whole command took 7 minutes. The CPU renders a v1 scene (three cameras at 1024 x 768, 64 samples with the denoiser) in about 50 seconds, the GPU in about 15. Raw numbers: out/verify_showcase/showcase.json.
