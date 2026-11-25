# NOW

**Updated:** 25 Nov 2026, 17:45 IST (brief VTF-07 running, unattended)
**Stage:** **Stage 5 prep on frozen v1 (brief VTF-07).** Step 0 is done and pushed; three lanes run in parallel: A (README, quick recipe, `vtf verify` showcase, fresh-clone check, VALIDATION / EVAL_PROTOCOL / ARCHITECTURE, Hugging Face release), B (breaking-point curves, counterfactual-twin flips, risk cards), C (photo-session guide, G8 registration, digital twins, sim-to-real script). Founder decisions recorded: Claude models only; v1 canonical at 300 scenes and 3,181 tasks (no v1.1, the last 200 scenes not rendered); cuts in BRIEF.md; 12 twin layouts.
**Deadline:** submissions close 6 Dec; our real deadline is the night of 5 Dec.

## Step 0 (done)

- **Blind audit, now spoiler-free:** out/v1/audit/index.html (same link) holds 150 tasks, none from the 18 scenes you saw in the Explorer (twins counted with their source scene, so 22 tasks were swapped, each for a fresh task of the same family, camera and conflict stratum). 46 conflict tasks. The browser starts a fresh answer sheet.
- **Twin kit trimmed:** 12 layouts (docs/twins/layouts.pdf), 6 kinds of item, 8 pieces (docs/twins/shopping_list.md and DEMO.md). The can and the spray bottle colours now match what the questions say (blue can, white spray bottle).

## Headline (docs/reports/v1/findings.md, chart in docs/media/shortcut_conflict_accuracy.png)

- Four Claude settings answered the same 1,567 tasks (the 1,500-task eval sample plus the 150 audit tasks) with only the image and the question, through the API: same prompts, schema and scorer; effort set on every call. Overall: Haiku 5.5 high 50.3%, Sonnet 5.5 high 61.6%, Opus 5.5 high 65.5%, Opus 5.5 xhigh 66.4%.
- **The 2D-shortcut gap is about 25 points for every model** (95% intervals from a scene bootstrap): Haiku 55% vs 30%, Sonnet 68% vs 44%, Opus high 72% vs 46%, Opus xhigh 74% vs 48% (shortcut agrees vs conflicts; 558 and 234 tasks).
- **Shortcut capture vs chance:** of conflict tasks a model got wrong, the share answered with exactly the 2D-shortcut answer: Sonnet 64% vs 46% chance (1.39x, 1.18 to 1.67), Opus high 1.36x, Opus xhigh 1.48x (1.21 to 1.84); Haiku is at chance (0.95x; its misses are more random). The pull is strongest on nearest to gripper: 22 of 27 Opus xhigh misses name the object nearest in the image (chance 17%).
- **Confound check: the gap survives.** Inside the same family and camera, conflict tasks score 25 to 28 points lower for every model; a logistic regression holding visible fraction, G4 margin, object count, camera quality and family fixed gives conflict odds ratios of 0.22 to 0.29 (all intervals well below 1; stable fits).
- **More thinking does not close it:** Opus xhigh vs high changes the gap by +0 points (-5 to +5) and conflict accuracy by +2 (-3 to +7), although xhigh thinks twice as long on conflict tasks (550 vs 251 thinking tokens). Bigger models lift conflict accuracy (Haiku to Opus +16 points) but not the gap.

## What changed tonight (all on main)

- **Sweep (P22a):** Message Batches path with the standard request body (byte-identical, tested), thinking tokens and call details on every result, a 100-task gate per model (error budget, cost projection), a resumable detached driver (scripts/model_sweep.py; state in out/v1/sweep/state.json). 4 x 1,567 answers (4,768 of them new tonight), 0 errors, 0 truncations; no fallback was needed. Results: out/v1/sweep/<model>/. The adapter refuses any Fable or Mythos model ID.
- **Analysis (P22b):** scripts/shortcut_findings.py writes findings.json, findings.md and the chart; the dataset report (docs/reports/v1/index.html) has a findings section. scripts/audit_vs_models.py puts your blind audit beside every model on the same 150 tasks; it runs as soon as you export the audit.
- **Task Explorer v1 (P25), live:** https://vtf-task-explorer.vercel.app with all four Claude settings and the blind baseline, and a conflict gallery (?view=gallery) of 12 scenes where every model took the shortcut, true answer in blue and the shortcut object in orange on the image and in 3D. 18 scenes, 10.6 MB (compressed images and glTF).
- **Swap cause (OPEN 15, answered):** no Blender leak across scenes (resident size flat at 1.1 to 1.3 GB over a 5-scene batch). Each Cycles render takes about 6.6 GB (mostly GPU memory) on top of about 14 GB held by other apps, leaving 150 to 400 MB free on this 24 GB Mac; anything more pushes macOS into swap.
- **Three-scene crash (OPEN 14, fixed):** the real crash was an empty point set in the lens undistort, reached from G2's support check. All 300 scenes re-validated into out/v1_1_check: the other 261 scenes give identical tasks and answers; 36 new tasks only in scenes 0130, 0174 and 0229 (diff in docs/reports/v1/v1_1_check_diff.json), kept for v1.1 in stage 4 (P23a).
- 278 tests pass.

## Running cost of model calls

- Tonight, all batched at half price: Haiku 5.5 $0.23 (projected $0.23 after its first 100), Sonnet 5.5 $0.15 (67 new audit tasks), Opus 5.5 high $7.20 (projected $6.98), Opus 5.5 xhigh $11.52 (projected $11.83). Tonight $19.10.
- Before tonight about $9.65. Total so far about $28.75.

## Asks

- **Blind audit first, then the Explorer:** the Explorer and the findings now show true answers, so please do the 150-task blind audit (out/v1/audit/index.html in the main checkout) before opening them. Export the JSON and say where it is; I score it and put you beside every model.
- **API keys:** the Anthropic key was rotated on 24 Nov and works (checked with one tiny call); revoke the old key in the Anthropic Console. The Hugging Face token was also pasted in chat: rotate it before the dataset upload.
- **Commit signing works again** (key loaded 25 Nov). After a restart, run `ssh-add --apple-use-keychain ~/.ssh/id_ed25519_github` once in the Terminal panel.
- **Shopping list:** 15 items, 23 pieces, in DEMO.md; print docs/twins/mat_A3.pdf at 100%.
- **Before the last 200 scenes:** restart, quit Docker Desktop and Chrome (memory, see OPEN 15), keep the 20 GB disk floor (39 GB free now). Resume, which re-renders nothing: `cd .claude/worktrees/run-v1 && uv run python scripts/scale_run.py --recipe recipes/v1.yaml --n 500 --seed 1 --out ../../../out/v1 --batch 25 --gltf --twin-fraction 0.2`, then balance, eval and report again.
- Turn on two-factor authentication for Vercel: https://vercel.com/account/settings/authentication?setup=authenticator

## Next

1. Your blind audit; then `scripts/audit_vs_models.py`, and fix every miss at its source.
2. Stage 4: Google, OpenAI and open models on the same 1,567 tasks once their keys are confirmed (same harness, a 100-task gate each); risk cards (P24).
3. v1.1: fold in the 36 tasks of the three fixed scenes (P23a); decide on the last 200 scenes.
