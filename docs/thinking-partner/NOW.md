# NOW

**Updated:** 23 Nov 2026, 23:55 IST
**Stage:** **Stage 3, Core factory: built, integrated and run (brief 05).** The scale run stopped at the founder's 20 GB disk floor after 300 of 500 scenes; eval, baselines, report and audit set are done on those.
**Deadline:** submissions close 6 Dec; our real deadline is the night of 5 Dec.

## Dataset v1 candidate (out/v1, main checkout)

- 300 scenes x 3 cameras (front, overhead, wrist), recipe v1, seed 1, 0 render failures. Stopped before batch 13 of 20 by the disk floor (19.84 GB free at 22:10).
- 7,013 candidate questions; 3,475 accepted plus 484 G6 twin tasks; after G5 rebalancing **3,181 tasks** in manifest.jsonl.
- Gate pass rates over 3,653 gated candidates: G1 100%, G2 95.4%, G3 99.5%, G4 99.9%, G7 100%. G6: 872 tasks sampled, 484 valid twins, all 484 changed their answer (299 had no twin rule, 89 twins were invalid, mostly the twin's own settle).
- G5: the prior-only baseline (most common answer per family) scores 25%; Claude Sonnet 5.5 with no image scores 15% (27 of 180), so the image is needed.
- Dataset report: docs/reports/v1/index.html (gate rates, rejection reasons, coverage, answer distributions, shortcut_conflict share, model splits). Rejections are mostly occlusion that is too weak, no candidate, hidden evidence and near-ties.

## Headline finding (Claude Sonnet 5.5, 1,500 stratified tasks, $6.80)

- 61.9% overall (95% interval 60 to 64%). By camera: front 65%, wrist 66.5%, overhead 54%.
- **shortcut_conflict:** 68.2% when the 2D shortcut agrees with the 3D truth, 45.1% when it does not (96 of 213). Nearest to gripper 59% vs 15%; front-to-back 93% vs 68%; free space 81% vs 50%. 65 of the 117 misses on conflict tasks are exactly the 2D-shortcut answer.
- By family: front-to-back 86%, count 76%, count by colour 74%, free space 71%, support 67%, gripper view 65%, reachability 60%, containment 60%, relation 51%, occlusion 47%, nearest to gripper 47%, distance in cm 27%.
- Nearest-to-gripper re-run (40 primitive scenes, key light, low gripper; out/nearest_v1): 68% vs 42% in stage 2; 0 of 7 on conflict tasks, every miss the shortcut object.

## What exists (all on main)

- Contracts v0.2 and spec/families.md; 40 Poly Haven objects and 4 exact references; sampler v1; three cameras with a quality dial; all 12 families with the naming rule, shortcut_conflict and twin rules; gates G1 to G7; incremental validation and the resumable scale driver with the disk floor; G5 balance and blind baseline; eval sampling with splits; the audit page; the dataset report; 262 tests pass.
- Task Explorer, public, now showing the v1 run (8 scenes, three cameras, 3D, Sonnet's answers, gates): https://vtf-task-explorer.vercel.app
- Physical twins: docs/twins/ (mat PDFs, 15-item shopping list, 30 layout sheets).

## Running cost of model calls

- Stage 2: $0.25. Tonight: $9.40 (20-scene check $1.31, nearest re-run $0.30, blind baseline $0.17, v1 eval $6.80, smoke test under $0.01). Total so far about $9.65.

## Asks

- **Blind audit, 150 tasks:** open out/v1/audit/index.html on the laptop (main checkout), answer, export the JSON, and say where it is; I score it with `vtf audit score`. The 12 stage-2 nearest-to-gripper tasks are ready at out/audit_s2/index.html.
- **Shopping list:** 15 items, 23 pieces, in DEMO.md; print docs/twins/mat_A3.pdf at 100%.
- **Disk:** 23 GB free at 23:55. Docker Desktop's VM disk holds 41 GB and was written tonight; trimming it (Docker Desktop, Troubleshoot, Clean / Purge data) or freeing about 3 GB more lets the last 200 scenes run. Resume (re-renders nothing): `cd .claude/worktrees/run-v1 && uv run python scripts/scale_run.py --recipe recipes/v1.yaml --n 500 --seed 1 --out /Users/hrishikesh/code/hrishikesh-visual-task-factory/out/v1 --batch 25 --gltf --twin-fraction 0.2`, then `vtf balance`, eval and report again.
- Turn on two-factor authentication for Vercel: https://vercel.com/account/settings/authentication?setup=authenticator

## Next

1. Founder audit; fix every miss at its source.
2. Stage 4 (P22): pin the frontier and open model IDs, a 50-task cost pilot, then the full evaluation.
