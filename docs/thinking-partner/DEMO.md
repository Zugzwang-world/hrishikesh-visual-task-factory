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
| Hero renders: one workcell from all three cameras | Hook, slides title | To do (stage 3) |
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
| Task Explorer screen recording | Live-demo backup | To do (stage 5) |
