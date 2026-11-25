# NOW

**Updated:** 25 Nov 2026, 18:50 IST (end of the unattended run, brief VTF-07)
**Stage:** **Stage 5, demo pack, on frozen v1.** Everything a judge runs and reads now exists: a README for judges, a one-command quickstart that passes from a fresh clone, the `vtf verify` showcase, VALIDATION / EVAL_PROTOCOL / ARCHITECTURE, two new findings and the risk cards, the dataset on Hugging Face, and the Explorer with a "How it's verified" panel. The physical twins are ready for the photo session. What remains is yours (blind audit, photos), then slides and the video.
**Deadline:** submissions close 6 Dec; our real deadline is the night of 5 Dec.

## Decisions in force (founder, 25 Nov)

- Claude models only: Haiku 5.5 high, Sonnet 5.5 high, Opus 5.5 high and xhigh. Never Fable or Mythos. Findings speak only about the Claude models we tested.
- v1 is canonical: 300 scenes, 3,181 tasks. No v1.1; the three-scene fix is repeatability evidence. The last 200 scenes are not rendered.
- Cut: Dockerfile, multi-view consistency, vision gain per model, paired model tests, a separate findings HTML. The slide media pack moves to the demo run.

## What exists now (all on main)

- **Spoiler-free blind audit:** out/v1/audit/index.html in the main checkout, same link as before. 150 tasks, none from the 18 Explorer scenes (22 swapped within their strata), 46 conflict tasks. Every model has answered all 150, so your score sits beside theirs the moment you export.
- **Judges' path:** [README.md](../../README.md). `uv run vtf quickstart`: 7 scenes, 21 images, 112 tasks in all 12 families, gates and a report; fake-oracle without a key, Sonnet with one. A fresh clone runs to a verified result in 4 min 15 s ([quickstart_check.md](../reports/quickstart_check.md)).
- **Repeatability:** [verify_showcase.md](../reports/v1/verify_showcase.md). Three frozen scenes re-rendered on GPU and CPU: scene files, settled poses, masks, depth, all 39 answers and the G7 hashes are exact. Images are near-exact (GPU SSIM 0.998 to 0.9999; CPU renders mostly byte-identical). It also caught a renderer material bug in batch renders that never touches answers (OPEN 16; disclosed, v1 kept as frozen).
- **Docs:** [VALIDATION.md](../VALIDATION.md), [EVAL_PROTOCOL.md](../EVAL_PROTOCOL.md), [ARCHITECTURE.md](../ARCHITECTURE.md).
- **New findings** ([findings.md](../reports/v1/findings.md) sections 5 and 6; charts docs/media/breaking_point.png and twin_flip.png):
  - Breaking points: hiding the evidence (under 60% visible) costs every Claude setting 10 to 13 points; a harsh camera costs Sonnet and Opus 12 to 16 points; every 10 more objects cuts the odds of a right answer to x0.73 to x0.83.
  - Counterfactual twins: on all 484 pairs, both answers are right for 34% (Haiku) to 52% (Opus high). Given the original right, 9 to 15% repeat the old answer. Opus xhigh is no better than high.
- **Risk cards:** [risk_cards.md](../reports/v1/risk_cards.md), one column per Claude setting: risk, severity, triggers, rule-based error types (shortcut answer, object not in the scene, count off by one), two example images each, and a mitigation.
- **Hugging Face:** https://huggingface.co/datasets/Hrishi1802/hrishikesh-visual-task-factory. Public, 2.54 GB, 29,381 files, CC0 data card with the frozen hashes.
- **Task Explorer redeployed:** https://vtf-task-explorer.vercel.app, now opening with the "How it's verified" panel.
- **Physical twins ready:**
  - 12 layouts, of which 11 are to shoot (L08 skipped: its bowl contents never settle in the physics simulation).
  - 6 kinds of item, 8 pieces. Guide: docs/twins/photo_session.pdf (about 55 minutes).
  - G8 proven on the digital twins: 36 of 36 correct views pass and 3 of 3 misplaced views are rejected, with pose error under 1 mm median ([g8_digital_check.md](../twins/g8_digital_check.md)).
  - The sim-to-real script runs as soon as photos land.
- 294 tests pass; four frozen v1 hashes unchanged; commit signing worked all run.

## Running cost of model calls

- Today: 1,920 batched twin answers $7.02, quickstart Sonnet check $0.09. Before today about $28.75. Total about $35.86.

## Asks

- **Blind audit:** open out/v1/audit/index.html in the main checkout, answer the 150, export the JSON, and say where it is.
- **Photo session:**
  - Print docs/twins/mat_A3.pdf at 100%.
  - Buy the 8 pieces (DEMO.md).
  - Follow docs/twins/photo_session.pdf.
  - AirDrop the photos into twins/inbox/ in the main checkout and say so.
- **Keys:** revoke the old Anthropic key in the Anthropic Console, if not done yet.
- Turn on two-factor authentication for Vercel: https://vercel.com/account/settings/authentication?setup=authenticator

## Next

1. Your audit: `scripts/audit_vs_models.py`, and every miss traced to its cause.
2. Your photos: `vtf twin register`, then `vtf twin sim-to-real` for the rendered-to-real gap.
3. Slides on the organizers' template, the media pack and the demo video (P28), then the submission form (P29).
