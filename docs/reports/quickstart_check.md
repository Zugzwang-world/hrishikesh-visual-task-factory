# Quickstart check: a fresh clone, following the README literally

The README quickstart was run as written in a new temporary folder (`mktemp -d`), cloned from GitHub, with no `.env` and `ANTHROPIC_API_KEY` unset, so the evaluation takes the `fake-oracle` path. Machine: Apple M4 (10 cores, 24 GB), macOS 15.7.3, Blender 5.3.0 with the Metal GPU, uv 0.13.1. Times exclude waiting for this machine's shared Blender lock (other renders were queued). The temporary clone was deleted afterwards.

```bash
git clone --branch <branch> https://github.com/Zugzwang-world/hrishikesh-visual-task-factory.git
cd hrishikesh-visual-task-factory
uv sync
uv run vtf quickstart
uv run vtf verify out/quick --sample 2          # run 1 used --device cpu, as the README then said
```

## Run 1 (25 Nov 2026, 18:10 IST, commit f18d853)

| Step | Time | Result |
| --- | --- | --- |
| `git clone` | 4 s | |
| `uv sync` | 1 s | uv's package cache was warm on this machine; a cold machine downloads the wheels once |
| `uv run vtf quickstart` | 308 s | Fetched 13 Poly Haven models and the HDRI (14 assets, 18 MB, 86 s). 7 scenes, 21 images; 95 tasks plus 17 G6 twin tasks in all 12 families; 85 candidate questions rejected with reasons; gate pass rates G1 100%, G2 95%, G3 100%, G4 100%, G6 100%, G7 100%. fake-oracle 112 of 112 (the scorer check). Dataset report and contact sheet written |
| `uv run vtf verify out/quick --sample 2 --device cpu` | 77 s | Scene files, settled scenes, object index, depth, masks, answers (16 tasks) and G7 hashes all matched. Beauty SSIM 0.9972 to 0.9995 against the GPU-rendered originals, so the single verdict line said `MISMATCH` |
| Disk | | out/ 91 MB, assets/cache 18 MB, .venv 338 MB |

**Same seed, two checkouts.** The clone's `out/quick` against the one made earlier in the main checkout from the same command: 7 of 7 scene files and settled scenes byte-identical; 21 of 21 object-index and depth arrays equal; `manifest.jsonl` and `rejected.jsonl` byte-identical (112 task IDs, every answer the same). Beauty images: SSIM 0.9981 to 0.9999 on the GPU, none byte-identical.

**Fixed after run 1:**

- The README's verify step re-rendered on the CPU and compared against GPU originals, which can only be near-exact. It now verifies on the default device (the GPU, like the quickstart) and says what to expect.
- `vtf verify` printed one verdict, so image noise under the 0.999 SSIM target read as `MISMATCH` although every truth check matched. It now prints the truth verdict (scene files, settled scenes, index, depth, masks, answers, G7 hashes) and the image similarity on separate lines.

## Run 2 (25 Nov 2026, 18:24 IST, commit 7134cb5): passes

| Step | Time | Result |
| --- | --- | --- |
| `git clone` | 3 s | |
| `uv sync` | 0 s | Warm uv cache |
| `uv run vtf quickstart` | 230 s | Same 14 assets fetched (35 s this time); everything else as in run 1: 7 scenes, 21 images, 95 tasks plus 17 twin tasks in 12 families, 85 rejections, the same gate pass rates, fake-oracle 112 of 112 |
| `uv run vtf verify out/quick --sample 2` | 22 s | `verify: truth ALL MATCH (scene files, settled scenes, object index, depth, masks, answers, G7 hashes)`; `images (rule C, near-exact): min beauty SSIM 0.998883; 2 of 4 below 0.999 (original on GPU, re-render on GPU)` |

Total 4 minutes 15 seconds from `git clone` to a verified run. Against the main checkout's `out/quick`, run 2 again matched byte for byte in every scene file, settled scene, index and depth array, task ID, answer and the rejection log; beauty SSIM 0.9979 to 0.9999.

Fixed after run 2: the quickstart's closing hint still suggested `--device cpu`; it now prints the same verify command as the README.

**What this shows.** The README quickstart works as written on a fresh clone, with no API key, in about 4 to 5 minutes on this Mac, and the factory is repeatable across checkouts: the same recipe and seed give the same scene files, tasks, answers and rejections. Images are near-exact on the GPU (SSIM 0.998 to 0.9999), below the brief's 0.999 target for some images, as recorded in [v1/verify_showcase.md](v1/verify_showcase.md). Not tested here: a machine without a GPU (Blender then renders on the CPU, about four times slower per scene) and Linux.
