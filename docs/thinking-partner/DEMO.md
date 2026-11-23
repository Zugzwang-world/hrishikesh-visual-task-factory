# DEMO

The storyline for the demo video and the 20 Dec live demo, plus the assets to collect as we build. Skeleton for now; fill in as stages land.

## Storyline (about 5 minutes)

1. **Hook: beyond 2D.** Most teams bring charts and documents. Robots see the physical world, and a spatial mistake becomes a wrong grasp or a collision. Show one workcell render next to a 2D chart: "we went one dimension further."
2. **The problem.** Vision-language models now drive robot perception, and they struggle with depth, distance, occlusion and counting in clutter. AI image generators can't even guarantee counts, so they can't make the answer key. A 3D engine knows where every object is.
3. **Task Explorer, live.** Rotate a 3D scene, switch between the overhead, front and gripper cameras, open a question, show its true answer and every model's answer side by side.
4. **Proof, live: `vtf verify`.** Rebuild 20 random tasks from their seeds on stage, show matching hashes, then open one rejected task and the gate that threw it out.
5. **The findings.** Skill-by-model accuracy, breaking-point curves (clutter, occlusion, camera quality), error attribution, and one model risk card in Innodata's T&E vocabulary.
6. **Physical twins.** A real photo of a real table arranged from a generated layout, with exact answers, and the rendered-to-real accuracy gap.
7. **Close: ready to adopt.** YAML recipes, bring-your-own objects and models, Docker, exports for training data and verifiable rewards. Built for Innodata's physical-AI bet.

## Assets to collect as we go

| Asset | For | Status |
| --- | --- | --- |
| First smoke render (beauty, object index, depth side by side) | "It renders headless" beat; README | Done: `out/smoke/preview.png` (regenerate with the smoke script; out/ is gitignored) |
| Hero renders: one workcell from all three cameras | Hook, slides title | Done (stage 3): [kitchen front](../media/hero_v1_kitchen_front.jpg), [overhead](../media/hero_v1_kitchen_overhead.jpg), [gripper](../media/hero_v1_kitchen_gripper.jpg); [workshop front](../media/hero_v1_workshop_front.jpg), [overhead](../media/hero_v1_workshop_overhead.jpg), [gripper](../media/hero_v1_workshop_gripper.jpg). Stage 2 front views: [scene 0](../media/hero_scene_0000_front.png), [scene 13](../media/hero_scene_0013_front.png) |
| Stage 3 contact sheet v2: 24 tasks over three cameras with Sonnet's answers | Proof beat; findings teaser | Done: [docs/media/contact_sheet_v2.jpg](../media/contact_sheet_v2.jpg) (20-scene check run, 15/24 shown correct) |
| Asset sheet: the 44 objects | Variation story | Done: [docs/media/assets_sheet.jpg](../media/assets_sheet.jpg) |
| Camera-quality dial: clean, mild, harsh, CCTV | Variation story | Done: [docs/media/camera_dial_sample.jpg](../media/camera_dial_sample.jpg) |
| Sampler v1 ray-cast preview (exact second route) | Proof beat | Done: [docs/media/sampler_v1_raycast.jpg](../media/sampler_v1_raycast.jpg) |
| The shortcut finding: nearest-to-gripper conflict tasks | Findings beat | Stage 3 re-run: 0 of 7 conflict tasks right, every miss the 2D-shortcut object (RESEARCH.md); larger split from the 500-scene run |
| Stage 2 contact sheet: 24 tasks with question, true answer, model answer, tick or cross | Proof beat; findings teaser | Done: [docs/media/stage2_contact_sheet.png](../media/stage2_contact_sheet.png) (Claude Sonnet 5.5, 20 of 24 shown correct) |
| The 2D-proximity miss: scene 0, gripper tip directly above the black box, model answers "green cone" | Findings beat: why robots need exact depth | Found in stage 2 (`out/s2`, task te10076218634b219); make a callout image in stage 4 |
| Turntable GIFs of 2–3 scenes | Hook, README, slides | To do (stage 3) |
| Clutter ladder: same scene at 3, 10, 25 objects | Variation story | To do (stage 3) |
| Counterfactual twin pair (before/after, answer flips) | Originality beat | To do (stage 3) |
| Gate report screenshot and a rejected-task example | Proof beat | To do (stage 3) |
| `vtf verify` terminal recording | Proof beat | To do (stage 3) |
| Skill x model accuracy chart with intervals | Findings | To do (stage 4) |
| Breaking-point curves per difficulty control | Findings | To do (stage 4) |
| Model risk card (one per model) | Findings | To do (stage 4) |
| Physical-twin photo next to its render and layout | Physical-twin beat | To do (alongside 3–4) |
| Rendered-to-real gap chart | Physical-twin beat | To do (stage 4) |
| Task Explorer v0, live | Live demo | Public: https://vtf-task-explorer.vercel.app (stage-3 check run: 8 scenes, three cameras, 3D scenes, Sonnet's answers, gates). Rebuild: `uv run vtf export <run> --format explorer --out web/public/data --scenes 8`, then `vercel deploy --prod --yes` in web/ |
| Task Explorer screen recording | Live-demo backup | To do (stage 5) |

## Physical twins: shopping list (P19)

Print docs/twins/mat_A3.pdf at 100% (or the four tiles of mat_A4_tiled.pdf), then buy these 15 kinds of item, 23 pieces in all, in one supermarket trip. Each stands in for one 3D asset; anything within a fifth of the size given is fine. Pick plain items close to the colour given, because questions name objects by colour. Full sizes and ranges: docs/twins/shopping_list.md. Layouts to build: docs/twins/layouts.pdf (30 scenes).

1. Two large red apples, about 10 cm across (asset food_apple_01).
2. One pomegranate, about 11.5 cm across (food_pomegranate_01).
3. Three lemons, about 7 cm across and 9.5 cm long (lemon).
4. Three limes, about 6 cm across and 7.5 cm long (food_lime_01).
5. One large avocado, about 9.5 cm across and 14 cm long (food_avocado_01).
6. Two large kiwi fruit, about 7 by 9 cm (food_kiwi_01).
7. Two brown onions, about 7 by 8.5 cm (yellow_onion).
8. One small bowl, wooden if possible, about 13 cm across and 7 cm tall (wooden_bowl_02).
9. Two food tins with a red label and a flat lid, about 9 cm across and 13 cm tall (cleaner_tin_01).
10. One black spray can, about 7 cm across and 17 cm tall (leather_cleaner_can).
11. One silver or grey spray can, about 6.7 cm across and 16.5 cm tall (lubricant_spray).
12. One grey or clear trigger spray bottle, about 23 cm tall (multi_cleaner_bottle).
13. One white bleach or detergent bottle, about 29 cm tall (bleach_bottle).
14. One small white roll of medical or masking tape, about 4.5 cm across (medical_tape).
15. One yellow rubber duck, about 11 cm tall (rubber_duck_toy).

Also: sticky tape, and a phone stand or a stack of books for the two camera spots.
