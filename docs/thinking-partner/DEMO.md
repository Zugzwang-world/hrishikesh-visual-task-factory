# DEMO

The storyline for the demo video and the 20 Dec live demo, plus the assets to collect as we build.

**The current story is [docs/DEMO_STORY.md](../DEMO_STORY.md)** (founder, 25 Nov): the founder on camera at the desk with the physical set, the 2D-shortcut trap, the factory, two endings for the real photos. The demo pack built from it (brief VTF-09):

- **Slides:** [docs/slides/vtf_slides.pptx](../slides/vtf_slides.pptx) and [.pdf](../slides/vtf_slides.pdf), 12 slides on the organizers' template plus its thank-you card. Build: `uv run scripts/build_slides.py`, render check: `uv run scripts/render_slides.py`.
- **Video kit:** [script.md](../video/script.md) (exact lines), [shot_list.md](../video/shot_list.md) and [.pdf](../video/shot_list.pdf), captions, all from [edit.yaml](../video/edit.yaml) (`uv run scripts/video_kit.py docs`); screen segments in docs/video/segments/ (`scripts/record_screens/`); assembly `scripts/make_video.sh` (proof: `--placeholders`).
- **Media pack:** [docs/media/pack/](../media/pack/).
- **Form text:** [docs/submission.md](../submission.md). **Judge Q&A:** [docs/QA.md](../QA.md).
- **Phase 2 fills the numbers:** `uv run scripts/refresh_numbers.py` (then `--check`).

## Submission rules (read 25 Nov 2026, brief VTF-09)

From the organizers' public page, [hack2skill.com/event/innodata](https://hack2skill.com/event/innodata) (it renders only with JavaScript; read headless):

- Deadline: **6 December 2026, 11:59 PM IST** (registration and submission).
- What to submit (FAQ): a working AI solution for the chosen challenge, with source code, setup instructions, sample outputs, validation evidence and a brief technical explanation, through the Participant Dashboard.
- Ask Me Anything session: **2 December 2026**, the place to ask about anything below.
- Team size 1 to 4; solo is allowed (FAQ). A third-party listing (hiretoday.in) says 3 to 4; the official page wins.
- Judging: Working Implementation 25%, Accuracy and Validation 25%, Meaningful Variation 20%, Originality and Engineering 15%, Practical Impact and Clarity 15%.
- Form fields (our dashboard notes of 23 Nov, BRIEF.md Part 4): challenge choice, prototype brief, technologies used, public GitHub link, demo video link, slides on the mandatory template.

Not stated anywhere public, so **assumed and unverified** (asked about in NOW.md): the video is at most 3 minutes; the video is shared as an unlisted YouTube or Google Drive link; the slides as a link (Google Slides, "anyone with the link can view"); no character limits on the form (the briefs are 398 and about 1,530 characters).

## Storyline, first draft (about 5 minutes; superseded by DEMO_STORY.md for the video, still the live-demo order)

1. **Hook: beyond 2D.** Most teams bring charts and documents. Robots see the physical world, and a spatial mistake becomes a wrong grasp or a collision. Show one workcell render next to a 2D chart: "we went one dimension further."
2. **The problem.** Vision-language models now drive robot perception, and they struggle with depth, distance, occlusion and counting in clutter. AI image generators can't even guarantee counts, so they can't make the answer key. A 3D engine knows where every object is.
3. **Task Explorer, live.** Rotate a 3D scene, switch between the overhead, front and gripper cameras, open a question, show its true answer and every model's answer side by side.
4. **Proof, live: `vtf verify`.** Re-render two quickstart scenes on stage (22 s): scene files, masks, depth and answers match exactly, images near-exact; then open one rejected task and the gate that threw it out.
5. **The findings.** Skill-by-model accuracy, breaking-point curves (clutter, occlusion, camera quality), error attribution, and one model risk card in Innodata's T&E vocabulary.
6. **Physical twins.** A real photo of a real table arranged from a generated layout, with exact answers, and the rendered-to-real accuracy gap.
7. **Close: ready to adopt.** One-command quickstart, YAML recipes, bring-your-own objects and models, the dataset on Hugging Face, exports for training data and verifiable rewards. Built for Innodata's physical-AI bet.

## Assets to collect as we go

| Asset | For | Status |
| --- | --- | --- |
| First smoke render (beauty, object index, depth side by side) | "It renders headless" beat; README | Done: `out/smoke/preview.png` (regenerate with the smoke script; out/ is gitignored) |
| Hero renders: one workcell from all three cameras | Hook, slides title | Done (stage 3): [kitchen front](../media/hero_v1_kitchen_front.jpg), [overhead](../media/hero_v1_kitchen_overhead.jpg), [gripper](../media/hero_v1_kitchen_gripper.jpg); [workshop front](../media/hero_v1_workshop_front.jpg), [overhead](../media/hero_v1_workshop_overhead.jpg), [gripper](../media/hero_v1_workshop_gripper.jpg). Stage 2 front views: [scene 0](../media/hero_scene_0000_front.png), [scene 13](../media/hero_scene_0013_front.png) |
| Stage 3 contact sheet v2: 24 tasks over three cameras with Sonnet's answers | Proof beat; findings teaser | Done: [docs/media/contact_sheet_v2.jpg](../media/contact_sheet_v2.jpg) (20-scene check run, 15/24 shown correct) |
| Asset sheet: the 44 objects | Variation story | Done: [docs/media/assets_sheet.jpg](../media/assets_sheet.jpg) |
| Camera-quality dial: clean, mild, harsh, CCTV | Variation story | Done: [docs/media/camera_dial_sample.jpg](../media/camera_dial_sample.jpg) |
| Sampler v1 ray-cast preview (exact second route) | Proof beat | Done: [docs/media/sampler_v1_raycast.jpg](../media/sampler_v1_raycast.jpg) |
| The shortcut finding | Findings beat | **Done (brief 06):** [docs/media/shortcut_conflict_accuracy.png](../media/shortcut_conflict_accuracy.png) (the slide chart) and [docs/reports/v1/findings.md](../reports/v1/findings.md). Four Claude settings on the same 1,567 tasks: every model is about 25 points worse when the 2D shortcut conflicts with the 3D truth (Haiku 55 vs 30%, Sonnet 68 vs 44%, Opus high 72 vs 46%, Opus xhigh 74 vs 48%); the gap survives the matched and regression checks; xhigh thinking does not close it; on nearest to gripper 22 of 27 Opus xhigh misses name the object nearest in the image (chance 17%) |
| Dataset report | Proof beat | Done: [docs/reports/v1/index.html](../reports/v1/index.html) (gates, rejections, coverage, answer distributions, shortcut share, model splits) |
| Stage 2 contact sheet: 24 tasks with question, true answer, model answer, tick or cross | Proof beat; findings teaser | Done: [docs/media/stage2_contact_sheet.png](../media/stage2_contact_sheet.png) (Claude Sonnet 5.5, 20 of 24 shown correct) |
| The 2D-proximity miss: scene 0, gripper tip directly above the black box, model answers "green cone" | Findings beat: why robots need exact depth | Found in stage 2 (`out/s2`, task te10076218634b219); make a callout image in stage 4 |
| Turntable GIFs of 2–3 scenes | Hook, README, slides | Done (VTF-09): [turntable_1.mp4](../media/pack/turntable_1.mp4) (scene_0103, the trap), [turntable_2.mp4](../media/pack/turntable_2.mp4) (scene_0194, 24 objects), [turntable_1.gif](../media/pack/turntable_1.gif) (3.5 MB) |
| Clutter ladder: same scene at 3, 10, 25 objects | Variation story | To do (stage 3) |
| Counterfactual twin pair (before/after, answer flips) | Originality beat | Done: finding [docs/media/twin_flip.png](../media/twin_flip.png) (both answers right for 34% to 52% of 484 pairs); before/after pair (VTF-09) [twin_before_after.png](../media/pack/twin_before_after.png) and [seg04_twin_before_after.mp4](../video/segments/seg04_twin_before_after.mp4): scene_0013, the lemon moved out of the basket, truth no to yes, Opus 5.5 high keeps "no" |
| Gate report screenshot and a rejected-task example | Proof beat | Done (VTF-09): [gate_funnel.png](../media/pack/gate_funnel.png) (7,013 candidates to 3,181 shipped) and the report scroll [seg06_report_scroll.mp4](../video/segments/seg06_report_scroll.mp4) |
| `vtf verify` terminal recording | Proof beat | Done (VTF-09): [seg07_verify_terminal.mp4](../video/segments/seg07_verify_terminal.mp4) (the real run on out/quick, 15 s, replayed x3) and the quickstart [seg05_quickstart_terminal.mp4](../video/segments/seg05_quickstart_terminal.mp4) (305 s, x38); live: `uv run vtf verify out/quick --sample 2` |
| Skill x model accuracy chart with intervals | Findings | Claude part done: accuracy by family, camera and shortcut split per model, with scene-bootstrap intervals, in [findings.md](../reports/v1/findings.md); other providers wait for keys |
| Breaking-point curves per difficulty control | Findings | Done (25 Nov): [docs/media/breaking_point.png](../media/breaking_point.png): hidden evidence costs every Claude setting 10 to 13 points; a harsh camera costs Sonnet and Opus 12 to 16; odds x0.73 to x0.83 per 10 more objects |
| Model risk card (one per model) | Findings | Done (25 Nov): [docs/reports/v1/risk_cards.md](../reports/v1/risk_cards.md), one column per Claude setting with two example images each |
| Physical-twin photo next to its render and layout | Physical-twin beat | Pipeline ready (25 Nov): each passing photo gets a rendered sibling from the photo's own camera pose, with an overlay of every object's outline; waits for the photo session (docs/twins/photo_session.pdf) |
| Rendered-to-real gap chart | Physical-twin beat | Script ready: `uv run vtf twin sim-to-real out/twins_real --model <claude model>` (accuracy on photos vs renders, intervals, McNemar); waits for the photos |
| Task Explorer v1, live | Live demo | **Done (brief 06; verification panel added 25 Nov):** https://vtf-task-explorer.vercel.app, frozen v1 with Haiku, Sonnet, Opus high and Opus xhigh answers plus the blind baseline, and a **Conflict gallery** (https://vtf-task-explorer.vercel.app/?view=gallery): 12 scenes where every model took the 2D shortcut, with the true answer (blue) and the shortcut object (orange) marked on the image and in 3D. Demo path: open the gallery, pick "nearest to gripper", rotate the 3D view to show the true nearest object. Rebuild: the export command in web/README.md, then `vercel deploy --prod --yes` in web/ |
| Task Explorer screen recording | Live-demo backup | Done (VTF-09): [seg01_trap_rotate.mp4](../video/segments/seg01_trap_rotate.mp4) (scene_0103 trap, 3D orbit), [seg02_verified_panel.mp4](../video/segments/seg02_verified_panel.mp4), gallery stills in [docs/media/pack/](../media/pack/) |

## Physical twins: the trap to film (VTF-08)

Hero layout H1 (hero_0297), front spot: "Is there room to place the tape on the table between the bottle and the lime?" The answer is yes (a 6.9 cm gap, a 4.5 cm tape, nothing in the way, checkable on the printed grid); Sonnet 5.5 and Opus 5.5 both say no on the digital twin, because the tape, nearer the lens, looks bigger than the gap. H2 asks the same question with the opposite truth (the kiwi sits in the gap) and both models say yes. Render: [docs/twins/media/H1_hero_0297_front.jpg](../twins/media/H1_hero_0297_front.jpg); details: [docs/twins/trap_check.md](../twins/trap_check.md). The real photo goes beside it after the session.

## Physical twins: shopping list (P19, trimmed 25 Nov for 12 layouts; 2 pieces added for the hero layouts)

Print docs/twins/mat_A3.pdf at 100% (or the four tiles of mat_A4_tiled.pdf), then buy these 8 kinds of item, 10 pieces in all. Each stands in for one 3D asset; anything within a fifth of the size given is fine. Pick plain items close to the colour given, because questions name objects by colour. Full sizes and ranges: docs/twins/shopping_list.md. Layouts to build: docs/twins/layouts.pdf (12 scenes, chosen in docs/twins/selection.json).

1. Three limes, about 6 cm across and 7.5 cm long (food_lime_01).
2. One large kiwi fruit, about 7 by 9 cm (food_kiwi_01).
3. One brown onion, about 7 by 8.5 cm (yellow_onion).
4. One small bowl, wooden if possible, about 13 cm across and 7 cm tall (wooden_bowl_02).
5. One white trigger spray bottle, about 23 cm tall (multi_cleaner_bottle).
6. One small white roll of medical or masking tape, about 4.5 cm across (medical_tape).
7. NEW for the hero layouts: one lemon, about 9.5 by 7 cm (lemon).
8. NEW for the hero layouts: one purple plastic bottle (fabric softener, detergent or bleach), about 29 cm tall (bleach_bottle).

Also: sticky tape, and a phone stand or a stack of books for the two camera spots.
