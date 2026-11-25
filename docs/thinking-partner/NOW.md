# NOW

**Updated:** 25 Nov 2026, 20:10 IST (end of brief VTF-08 Phase 1, unattended)
**Stage:** **Stage 5, demo pack, on frozen v1.** Phase 1 of VTF-08 is done: the trap check, three hero layouts that fool both Sonnet and Opus on their digital twins, and the scale-stated fix with its re-ask. **Phase 2 waits for the founder:** the audit export and the photos.
**Deadline:** submissions close 6 Dec; our real deadline is the night of 5 Dec.

## Decisions in force (founder, 25 Nov)

- Claude models only: Haiku 5.5 high, Sonnet 5.5 high, Opus 5.5 high and xhigh. Never Fable or Mythos. Findings speak only about the Claude models we tested.
- v1 is canonical: 300 scenes, 3,181 tasks. Its four frozen hashes are unchanged. Programs 0.3.0 changes new runs only.
- out/v1/audit is not touched while the founder audits.

## What VTF-08 Phase 1 added (branch claude/vtf-08-phase-1-unattended-44adff, merged to main)

- **Trap check** ([docs/twins/trap_check.md](../twins/trap_check.md)): 8 conflict tasks among the 154 digital-twin tasks (1 on the front spot). Sonnet 89.0% and Opus 90.9% on the renders; both took the shortcut on only one conflict task.
- **Hero layouts H1 to H3:**
  - Picked from 103 sampled and 20 rendered five-object layouts.
  - Both models take the shortcut on each one's digital twin.
  - Their trap questions are pinned, so every hero photo asks them.
  - In the photo guide first, front spot only, three photos each.
- **Film H1:** "Is there room to place the tape on the table between the bottle and the lime?" Truth yes (a 6.9 cm gap, a 4.5 cm tape); both models say no. H2 asks the same question with the opposite truth, and both say yes.
- **Shopping:** two new pieces, a lemon and a purple plastic bottle. 8 kinds, 10 pieces in all.
- **Phase 2 dry run:** two hero renders through `vtf twin register` pass G8, are recognised, and get their pinned questions.
- **Scale stated:**
  - Programs 0.3.0: every new distance and free-space question opens with the grid sentence, or the reference object's exact size where there is no grid (53% of such tasks).
  - v1's 272 such eval and audit tasks were re-asked to all four settings, results apart in out/v1/sweep_scale/.
  - Sonnet +10 points (+4 to +15), Haiku +5, Opus high and xhigh +1. Findings section 8, [scale_stated.md](../reports/v1/scale_stated.md).
  - OPEN 18 closed. The audit comparison uses these answers.
- **New tools:**
  - scripts/ask_sweep.py: batched asks under a condition.
  - scripts/hero_layouts.py and recipes/hero_twin.yaml.
  - vtf.eval.scale.
  - Pinned questions (`<run>/pinned_questions.json`).
  - scripts/scale_findings.py.

## Running cost of model calls

- VTF-08 Phase 1: scale-stated re-ask $8.68, trap check $0.92, hero layouts $0.70, $10.30 in all. Before: about $35.86. Total about $46.16.

## Asks

- **Blind audit:** finish the 150 in out/v1/audit/index.html (main checkout), export the JSON, and say where it is.
- **Photo session:**
  - Print docs/twins/mat_A3.pdf at 100%.
  - Buy the 10 pieces on docs/twins/shopping_list.md; the lemon and the purple bottle are new.
  - Shoot H1 to H3 first (docs/twins/photo_session.pdf).
  - AirDrop the photos into twins/inbox/ in the main checkout and say so.
- **Keys:** revoke the old Anthropic key in the Anthropic Console, if not done yet.
- Turn on two-factor authentication for Vercel: https://vercel.com/account/settings/authentication?setup=authenticator

## Next (Phase 2, when the founder says the audit and photos are ready)

1. **Audit:** `uv run python scripts/audit_vs_models.py --run out/v1 --answers <export>`, using the scale-stated answers by default. Label every disagreement (our bug, ambiguous, human error). Human vs each model, with intervals.
2. **Photos:** `vtf twin register` (hero photos named H1_front_1.jpg or recognised), reshoot list in NOW.md.
3. **Sim-to-real:** all four settings on the real tasks and their siblings; say which ending we have.
4. **Video:** the H1 photo where the models say no, with its render beside it in the Explorer.
5. **Redeploy:** findings, risk cards, report, Hugging Face, Explorer, README. Then slides and the video (P28) and the form (P29).
