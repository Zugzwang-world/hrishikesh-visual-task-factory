# NOW

**Updated:** 23 Nov 2026, 22:15 IST
**Stage:** **Stage 3, Core factory: built and integrated; the scale run is going (brief 05, overnight).**
**Deadline:** submissions close 6 Dec; our real deadline is the night of 5 Dec.

## Scale run stopped at the disk floor (22:10 IST)

- The run stopped itself before batch 13 of 20, as the founder's rule says: 19.84 GB free, the next batch needed about 0.26 GB, floor 20 GB.
- Done: 300 of 500 scenes x 3 cameras, 0 render failures, 3,475 accepted tasks plus 484 G6 twin tasks. Gates: G1 100%, G2 95%, G3 99%, G4 100%, G6 100% of valid twins changed their answer, G7 100%.
- The run itself uses 3.1 GB. Free space fell from 30 GB at the start mostly outside out/: app caches regrew to 9 GB and the system temp folders hold 12 GB.
- Not resumed. Eval and the report carry on with the 300 scenes (the post-run chain).
- To finish the last 200 scenes: free space, then rerun the same command (it resumes at batch 13 and re-renders nothing): `cd .claude/worktrees/run-v1 && uv run python scripts/scale_run.py --recipe recipes/v1.yaml --n 500 --seed 1 --out /Users/hrishikesh/code/hrishikesh-visual-task-factory/out/v1 --batch 25 --gltf --twin-fraction 0.2`

## Running now (detached, survive the session)

- **Scale run (P18):** 500 scenes x 3 cameras from recipes/v1.yaml, seed 1, batches of 25, 20% G6 twins, into out/v1 (main checkout). Started 19:44 IST from a frozen copy of the code (.claude/worktrees/run-v1 at 4e850e9). Progress: out/v1/logs/progress.json and scale_run.log. Expected about 4.5 hours.
- **Nearest-to-gripper re-run:** 40 stage-2 style scenes with the key light and the low gripper, three cameras, into out/nearest_v1.
- **Sonnet on the 20-scene check run** (out/v1check).

## What exists (all on main)

- Contracts v0.2 and spec/families.md (12 families: truth rule, G2 pixel route, G4 margins, 2D shortcut heuristic, twin rule).
- 40 Poly Haven objects and 4 exact-size references, rendered with their own materials; settle with hull proxies; grid table; painted reach zones; key light with HDRI fill; 64 samples plus denoiser.
- Sampler v1: 3 to 25 objects, stacks, containers, zones, look-alikes, duplicates, the low gripper, three cameras (overhead, front, wrist), lighting conditions and a camera-quality dial (mild, harsh, CCTV).
- All 12 question families with two phrasings, the naming rule, shortcut_conflict on spatial families, and twin rules.
- Gates G1 to G7 (G2 recomputes every answer from pixels; G6 renders counterfactual twins), incremental validation, the resumable scale driver with your 20 GB disk floor.
- G5 balance and blind baseline, eval sampling with splits, the blind audit page, the dataset report.
- Task Explorer v0, public: https://vtf-task-explorer.vercel.app (stage-2 sample for now).
- Physical twins: mat PDFs, a 15-item shopping list, 30 layout sheets (docs/twins/).
- 262 tests pass.

## 20-scene check (out/v1check)

- 20 scenes x 3 cameras in 11 minutes: 246 tasks plus 44 twin tasks; one scene rejected by the settle check (7 cm drift).
- Gate pass rates: G1 100%, G2 94%, G3 100%, G4 97%, G6 100% of valid twins changed their answer, G7 100%.
- About 7 MB of disk per scene including twins.

## Next tonight

1. Sonnet accuracy on the nearest-to-gripper re-run, split by shortcut_conflict and camera.
2. When the scale run finishes: balance, blind baseline, Sonnet on a stratified 1,500 tasks, the dataset report, the audit set, a Task Explorer re-export.

## Running cost of model calls

- Stage 2: $0.25. Stage 3 so far: under $0.01 (blind-mode smoke test).

## Asks

- Turn on two-factor authentication for Vercel: https://vercel.com/account/settings/authentication?setup=authenticator
- Disk: caches cleared with your OK (14 GB freed); 30 GB free at 19:45. The scale run stops itself before going under 20 GB.
