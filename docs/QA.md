# Judge Q&A

Likely questions with short answers, each backed by a number and a link (brief VTF-09, section 6). For the live demo and the finalist interview. Numbers are from frozen dataset v1 unless marked; `<!--ph:...-->` comments mark the Phase 2 results that `scripts/refresh_numbers.py` fills in.

### 1. Why only Claude models?

Depth over breadth. We ran four Claude settings (Haiku 5.5, Sonnet 5.5, Opus 5.5 at effort high, Opus 5.5 at xhigh) on the same 1,567 tasks, with scene-bootstrap intervals and confound checks, rather than thin coverage of many providers. The batched sweep cost $19.10. Every claim says "the Claude models we tested". The harness is provider-neutral: another provider is one adapter (question 9). [EVAL_PROTOCOL.md](EVAL_PROTOCOL.md)

### 2. Is the 2D-shortcut trap fair?

Yes. The 3D answer is exact and visible: G3 requires the evidence in the image (99.5% pass), G4 rejects near-ties, and object names are unambiguous by rule. The shortcut flag is computed from the image geometry when a task is made, before any model runs; it is never tuned on model answers. The gap is not a harder subset: within the same family and camera, conflict tasks score 25 to 28 points lower, and a regression holding visibility, margin, clutter and camera quality fixed gives conflict odds ratios of 0.22 to 0.29. Distance questions state the grid's scale since programs 0.3.0. <!--ph:AUDIT-->{{AUDIT}}<!--/ph--> [findings.md, section 3](reports/v1/findings.md)

### 3. How is this different from CLEVR?

CLEVR (2017) rendered 3D toy shapes and answered questions with programs, as a fixed dataset. We keep the program answers and add what a robot needs: 44 real-world objects, physics-settled clutter, stacks and containers, overhead, front and gripper cameras, robot questions (nearest to the gripper, reach, free space), a second answer computed from the pixels, counterfactual twins, real photos of physical twins, and an evaluation harness. It is a generator, not a dataset. [README](../README.md)

### 4. What if real photos behave differently from renders?

Then the factory measures that gap, which is its job. `vtf twin sim-to-real` scores every setting on real photos and on their renders from the same camera pose, with layout-bootstrap intervals and McNemar's test. G8 rejects a photo whose objects are off their layout. <!--ph:SIM2REAL-->{{SIM2REAL}}<!--/ph--> [g8_digital_check.md](twins/g8_digital_check.md), [photo_session.md](twins/photo_session.md)

### 5. How repeatable is it?

Every random draw comes from the seed, and a task ID is a content hash. A fresh clone runs the quickstart (7 scenes, 112 tasks) in 4 min 15 s and matches another checkout byte for byte in scene files, masks, depth, task IDs and answers. `vtf verify` re-renders frozen v1 scenes: truth exact, images near-exact (GPU SSIM 0.998 to 0.9999). It even caught a renderer material bug, which we disclose. [quickstart_check.md](reports/quickstart_check.md), [verify_showcase.md](reports/v1/verify_showcase.md)

### 6. How would Innodata use it?

Three ways, all in its current lines of business. Test and evaluation: per-model risk cards in T&E terms (risk, severity, triggers, error types, mitigation). Physical-AI data: unlimited fresh scenes with exact labels across three cameras. Agent training: every answer is a program, so every task is a verifiable reward with no AI judge. [risk_cards.md](reports/v1/risk_cards.md)

### 7. What does it cost?

About $46 of model calls in all so far, batched at half price; the four-setting sweep of 1,567 tasks was $19.10, about a third of a cent per answer. Generating and checking tasks needs no API key: Blender on a laptop GPU, overnight for v1. The dataset is free on Hugging Face (2.54 GB, CC0). [NOW.md cost log](thinking-partner/NOW.md)

### 8. How do you add a new question family?

Write a typed program that answers it from the scene file and a question template (`packages/vtf/src/vtf/programs/`), a twin rule that changes the answer, and its pixel route and margins for G2 and G4 (written down in [spec/families.md](../spec/families.md)); then add it to a recipe's question mix. Every gate, the package writer, the eval and the reports pick it up unchanged. Twelve families were built this way.

### 9. How do you add a new model provider?

Subclass `Adapter` (one method: image, question and answer type in, raw text out), add a branch in `make_adapter`, and an entry in `configs/models.yaml`. Prompts, the JSON schema, the cache, the scorer and every report are shared, so a new provider's numbers are directly comparable. [packages/vtf/src/vtf/eval/adapters/](../packages/vtf/src/vtf/eval/adapters/)

### 10. How do you know the answers are right?

Twice, without AI. G1 runs the typed program on the settled scene file (100%); G2 recomputes the answer independently from the rendered object-index and depth pixels, and the 167 tasks where the two disagreed were rejected (95.4% agree). A fake oracle that echoes the truth scores 112 of 112 through the real scorer, which checks the checker. [VALIDATION.md](VALIDATION.md)

### 11. Do the questions really need the image?

Yes. G5: Sonnet answering with the question only scores 15%, below the 24.7% an always-guess-the-commonest-answer baseline gets after balancing; answer distributions are flattened per family so priors don't help. [VALIDATION.md, G5](VALIDATION.md)

### 12. Why should synthetic scenes say anything about real robots?

The scenes are photoreal (PBR assets, HDRI lighting, physics settle), the camera dial adds real camera faults (noise, blur, lens distortion, JPEG, CCTV), and the difficulty controls move model accuracy the way the real world would: evidence under 60% visible costs 10 to 13 points, a harsh camera costs Sonnet and Opus 12 to 16. Physical twins then test the same layouts on real photos (question 4). [findings.md, section 5](reports/v1/findings.md)

### 13. Doesn't more thinking or a bigger model fix it?

Bigger models raise accuracy overall (Haiku to Opus +15 points) but not the gap. Opus at xhigh effort thinks about twice as long on conflict tasks and the gap moves by 0 points (95% CI −5 to +5). Stating the grid's scale helps distances (Sonnet +10 points) but leaves the shortcut. [findings.md, section 4](reports/v1/findings.md), [scale_stated.md](reports/v1/scale_stated.md)

### 14. Do the models track the scene when it changes?

Not reliably. On 484 counterfactual twin pairs (one object moved, a new true answer), the settings get both versions right only 34% (Haiku) to 52% (Opus high) of the time, and on 9 to 12% of pairs they repeat the old answer. [findings.md, section 6](reports/v1/findings.md)

### 15. How big is it, and does it scale?

v1 is 300 rendered scenes (500 generated; the run stopped itself at the 20 GB disk floor), 7,013 candidate questions and 3,181 shipped tasks in 12 families from 3 cameras, with every rejection logged. The generator has no ceiling: more scenes are more seeds. [VALIDATION.md, the v1 funnel](VALIDATION.md)

### 16. What is the human baseline?

<!--ph:AUDIT-->{{AUDIT}}<!--/ph--> The founder answered 150 tasks blind (no truth and no model answers on the page), stratified by family, camera and shortcut conflict, scored by the same checker; every disagreement is labelled our bug, ambiguous, or human error.

### 17. What are the limits?

Only Claude models so far. One domain pack (the workcell). Re-rendered images are near-exact, not byte-identical. The v1 renderer material bug (answers, masks and depth unaffected) is disclosed. The multi-view consistency analysis was cut for time; the three cameras remain in the dataset. [README, limits](../README.md)
