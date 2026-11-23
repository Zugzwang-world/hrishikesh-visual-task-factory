# NOW

**Updated:** 24 Nov 2026, 01:00 IST (overnight, brief 06 running unattended)
**Stage:** **Stage 4 starts on frozen v1: the Claude model sweep, the shortcut analysis and Explorer v1 (brief 06).** Dataset v1 stays frozen at 300 scenes and 3,181 tasks (docs/reports/v1/FROZEN.json, unchanged).
**Deadline:** submissions close 6 Dec; our real deadline is the night of 5 Dec.

## Tonight, in progress (brief 06)

- Every model answers the same 1,567 tasks: Sonnet's 1,500-task sample plus the 150 audit tasks (67 were new; Sonnet answered them tonight). Results go to out/v1/sweep/<model>/; out/v1/eval/ is untouched.
- Phase A passed: smoke calls for claude-haiku-5-5, claude-opus-5-5 (high) and claude-opus-5-5 (xhigh, max_tokens 64,000) resolved, took the image and returned parseable JSON first time; a 5-task Message Batches proof came back 5 of 5 in 90 s with byte-identical request bodies. Disk 41 GB free (the restart cleared swap to 0).
- Phase B running detached (out/v1/logs/sweep_run.log, state in out/v1/sweep/state.json): Sonnet's 67 done; Haiku's 100-task gate had 0 errors or truncations; its other 1,362 tasks are in batches; Opus high, then Opus xhigh follow, each gated on its first 100.
- Phase C code is ready and tested on Sonnet: scripts/shortcut_findings.py (scene bootstrap, shortcut capture vs chance, matched strata, clustered logistic regression, headline chart), the findings section of the dataset report, and scripts/audit_vs_models.py for the founder's audit.
- Phase D (Explorer v1 with every model and a conflict gallery) is being built in a separate worktree.

## Running cost of model calls (tonight)

- claude-sonnet-5-5 (67 batched): $0.15. claude-haiku-5-5: $0.016 after its first 106; projected total $0.23. claude-opus-5-5 high and xhigh: one smoke call each, under $0.01. Batches bill at half price.
- Before tonight: about $9.65 (Sonnet's 1,500 answers were $6.80 of it).

## Dataset v1 candidate (out/v1, main checkout)

- 300 scenes x 3 cameras (front, overhead, wrist), recipe v1, seed 1, 0 render failures. Stopped before batch 13 of 20 by the disk floor (19.84 GB free at 22:10).
- 7,013 candidate questions; 3,475 accepted plus 484 G6 twin tasks; after G5 rebalancing **3,181 tasks** in manifest.jsonl.
- Gate pass rates over 3,653 gated candidates: G1 100%, G2 95.4%, G3 99.5%, G4 99.9%, G7 100%. G6: 872 tasks sampled, 484 valid twins, all 484 changed their answer (299 had no twin rule, 89 twins were invalid, mostly the twin's own settle).
- G5: the prior-only baseline (most common answer per family) scores 25%; Claude Sonnet 5.5 with no image scores 15% (27 of 180), so the image is needed.
- Dataset report: docs/reports/v1/index.html (gate rates, rejection reasons, coverage, answer distributions, shortcut_conflict share, model splits). Rejections are mostly occlusion that is too weak, no candidate, hidden evidence and near-ties.

## Attempt to finish the last 200 scenes (23:58, founder's request): stopped at the floor

- Resumed at batch 13 with 23.0 GB free (same frozen code). Within three minutes swap grew from 14 to 17 GB and free space fell to 19.9 GB, under the 20 GB floor; our batch writes only about 0.26 GB. Stopped the run and its post-run chain at once, per the rule. 4 scenes of batch 13 rendered, none validated; they resume later at no cost.
- The resume's collect steps had rebuilt the manifest; balance was re-run and gives the same 3,181 tasks, all 1,500 eval tasks included, so the eval and the report stand.
- **To finish:** restart the Mac (swap is 17 GB and is the cause; a restart clears it), then run the resume command below and the gated chain `zsh out/v1/logs/after_scale_run_2.sh` (it re-runs balance, the blind baseline, the eval with cached replies free, the report and the audit set, and the Explorer export only with 20 GB free).
- 3 of the 300 scenes (0130, 0174, 0229) failed validation on an empty-projection crash (an object wholly behind the wrist camera) and contribute no tasks; fix noted in OPEN.md item 14.

## Headline finding (Claude Sonnet 5.5, 1,500 stratified tasks, $6.80)

- 61.9% overall (95% interval 60 to 64%). By camera: front 65%, wrist 66.5%, overhead 54%.
- **shortcut_conflict:** 68.2% when the 2D shortcut agrees with the 3D truth, 45.1% when it does not (96 of 213). Nearest to gripper 59% vs 15%; front-to-back 93% vs 68%; free space 81% vs 50%. 65 of the 117 misses on conflict tasks are exactly the 2D-shortcut answer.
- By family: front-to-back 86%, count 76%, count by colour 74%, free space 71%, support 67%, gripper view 65%, reachability 60%, containment 60%, relation 51%, occlusion 47%, nearest to gripper 47%, distance in cm 27%.
- Nearest-to-gripper re-run (40 primitive scenes, key light, low gripper; out/nearest_v1): 68% vs 42% in stage 2; 0 of 7 on conflict tasks, every miss the shortcut object.

## What exists (all on main)

- Contracts v0.2 and spec/families.md; 40 Poly Haven objects and 4 exact references; sampler v1; three cameras with a quality dial; all 12 families with the naming rule, shortcut_conflict and twin rules; gates G1 to G7; incremental validation and the resumable scale driver with the disk floor; G5 balance and blind baseline; eval sampling with splits; the audit page; the dataset report; 262 tests pass.
- Task Explorer, public, now showing the v1 run (8 scenes, three cameras, 3D, Sonnet's answers, gates): https://vtf-task-explorer.vercel.app
- Physical twins: docs/twins/ (mat PDFs, 15-item shopping list, 30 layout sheets).

## Running cost of model calls (before tonight)

- Stage 2: $0.25. Tonight: $9.40 (20-scene check $1.31, nearest re-run $0.30, blind baseline $0.17, v1 eval $6.80, smoke test under $0.01). Total so far about $9.65.

## Asks

- **Commit signing:** the SSH agent was empty after the restart, so tonight's commits are unsigned (signing switched off per command only; your git config is unchanged). To sign future commits again, run `ssh-add ~/.ssh/id_ed25519_github` once after a restart (tonight's pushed commits stay unsigned; rewriting main's history is not worth it).

- **Blind audit, 150 tasks (46 of them shortcut-conflict tasks, all three cameras):** open out/v1/audit/index.html on the laptop (main checkout), answer, export the JSON, and say where it is; I score it with `vtf audit score`. The 12 stage-2 nearest-to-gripper tasks are ready at out/audit_s2/index.html.
- **Shopping list:** 15 items, 23 pieces, in DEMO.md; print docs/twins/mat_A3.pdf at 100%.
- **Disk:** 23 GB free at 23:55. Docker Desktop's VM disk holds 41 GB and was written tonight; trimming it (Docker Desktop, Troubleshoot, Clean / Purge data) or freeing about 3 GB more lets the last 200 scenes run. Resume (re-renders nothing): `cd .claude/worktrees/run-v1 && uv run python scripts/scale_run.py --recipe recipes/v1.yaml --n 500 --seed 1 --out /Users/hrishikesh/code/hrishikesh-visual-task-factory/out/v1 --batch 25 --gltf --twin-fraction 0.2`, then `vtf balance`, eval and report again.
- Turn on two-factor authentication for Vercel: https://vercel.com/account/settings/authentication?setup=authenticator

## Next

1. Founder audit; fix every miss at its source.
2. Stage 4 (P22): pin the frontier and open model IDs, a 50-task cost pilot, then the full evaluation.
