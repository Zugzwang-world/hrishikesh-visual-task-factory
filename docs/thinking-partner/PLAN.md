# Plan

Work items in build order. Status is todo, doing or done. Add, split or drop items whenever it helps; no approval needed. Concept and architecture: BRIEF.md.

## Stage 1: Setup

| ID | Item | Size | Done when | Status |
| --- | --- | --- | --- | --- |
| P01 | Repo, CLAUDE.md, roster, thinking-partner files, Blender smoke render | M | Smoke render and passes exist; repo public | done |
| P02 | Tracker: PLAN.md, CHECKLIST.md, update rule | S | Both files pushed | done |
| P02a | Finish setup: Node 26 LTS, Vercel CLI, Hugging Face CLI and empty dataset repo, plan libraries, Poly Haven proof render, determinism decision, parallel-work lanes | M | Tools proven; Poly Haven render exists; HF dataset repo exists | done |

## Stage 2: First scene (one task end to end)

| ID | Item | Size | Done when | Status |
| --- | --- | --- | --- | --- |
| P03 | Scene spec v0: pydantic SceneSpec (table, objects with asset ID, pose, scale, material; gripper; zones; cameras; lights; seed) plus JSON Schema export | M | spec/scene.schema.json generated; a sample scene file validates | done |
| P04 | Sampler v0: 5 to 10 procedural objects (boxes, cylinders, spheres) in distinct colours on a table, no overlaps, seeded | S | Same seed gives a byte-identical scene file | done |
| P05 | vtf-blender renderer v0: build the scene from the file, rigid-body settle, write settled poses back, render beauty, object-index and depth from one camera | L | One command turns a scene file into PNG, mask, depth and a settled scene file | done |
| P06 | Question programs v0: count, nearest to gripper, front-to-back; templates with two phrasings each; typed answers | M | Programs return answer plus the object IDs used as evidence | done |
| P07 | Gates v0 (G1 truth, G3 visibility from masks, G7 determinism) and the task package writer | M | out/run/tasks/ID/ holds image, task.json and gate results | done |
| P08 | Eval v0: one Anthropic model answers in JSON; typed scorer; SQLite cache | S | Accuracy printed for a small run; reruns hit the cache | done |
| P09 | First end-to-end run on about 20 tasks, plus a contact sheet (image, question, truth, model answer) saved for DEMO.md | S | Contact sheet exists; NOW.md says stage 2 done | done |

## Stage 3: Core factory

| ID | Item | Size | Done when | Status |
| --- | --- | --- | --- | --- |
| P10 | Assets: about 40 Poly Haven objects via their API, scale-normalised, real-world sizes in assets/manifest.yaml; procedural scale references; cm-grid table texture | M | Manifest lists every asset; each loads and renders | done |
| P11 | Sampler v1: clutter levels, stacks, containers, reach zones, look-alikes; difficulty controls in recipes | M | Recipe sliders change scenes as expected | done |
| P12 | Look: Poly Haven HDRI lighting, PBR materials, denoising, sample count tuned for speed | S | Hero renders saved for DEMO.md | done |
| P13 | Cameras: overhead, front and gripper rigs with jitter; post-render camera-quality dial (noise, blur, distortion, CCTV look) | M | Same scene renders from all three cameras at three quality levels | done |
| P14 | All 12 question families from BRIEF.md, each with a twin rule | L | Every family produces valid tasks and twins | done |
| P15 | Gates G2 (answer recomputed from mask and depth pixels), G4 (no near-ties), G5 (prior-only and question-only baselines), G6 (twins) | L | Gate report shows pass rates and rejection reasons | done |
| P16 | Audit page: a small local page that shows sampled tasks without answers and records the founder's answers | S | Founder can audit 150 tasks; accuracy computed | done (25 Nov: refreshed without the 18 Explorer scenes, 22 tasks swapped within their strata; 150 tasks, 46 shortcut-conflict; waiting on the founder) |
| P17 | Dataset report: gate pass rates, rejection reasons, coverage matrix, answer distributions | M | Static HTML report | done |
| P18 | Scale run: 500 scenes x 3 cameras, about 3,000 tasks (Mac overnight, batches of 25, resumable) | M | Dataset v1 candidate on disk with its report | done: v1 frozen at 300 of 500 scenes (20 GB disk floor), 3,181 tasks, report and freeze record in docs/reports/v1; v1 is canonical and the last 200 scenes are not rendered (decided 25 Nov) |

## Physical twins (alongside stages 3 and 4)

| ID | Item | Size | Done when | Status |
| --- | --- | --- | --- | --- |
| P19 | Placement mat PDF (cm grid, ArUco corner markers) and printable layout sheets for about 30 twin scenes built from real-object stand-ins | S | PDFs ready to print | done |
| P20 | Founder photo session: 12 layouts, about 3 photos each (trimmed 25 Nov, VTF-07), following docs/twins/photo_session.md | Founder | Photos in twins/inbox/ | todo (kit trimmed: 12 layouts, 6 kinds / 8 pieces) |
| P21 | G8: register each photo to its layout; package twin tasks; digital twins at the photo poses; sim-to-real script | M | Twin tasks pass G8; G8 proven on digital twins (one misplaced object rejected) | doing (VTF-07 lane C: prep before the photos) |

## Stage 4: Evaluate

| ID | Item | Size | Done when | Status |
| --- | --- | --- | --- | --- |
| P22 | Eval harness v1: Claude models only (decided 25 Nov, VTF-07): Haiku 5.5 high, Sonnet 5.5 high, Opus 5.5 high and xhigh; never Fable or Mythos; other providers only if judges ask | M | Pilot cost per task known | done: Message Batches at half price, thinking tokens and call details per result, cost per model after its first 100; provider interface kept for later |
| P22a | Claude sweep on frozen v1 (brief 06): Haiku 5.5 high, Sonnet 5.5 high, Opus 5.5 high and xhigh on the same 1,567 tasks | M | Every model's results.jsonl in out/v1/sweep/ | done: 4 x 1,567 answers, 0 errors, $19.10 tonight (batched at half price) |
| P22b | Shortcut analysis (brief 06): accuracy with scene intervals, shortcut capture vs chance, confound check, thinking effect, headline chart, findings section in the dataset report | M | docs/reports/v1/findings.md and the chart in docs/media/ | done; scripts/audit_vs_models.py waits for the founder's audit |
| P23 | Analyses on the Claude settings: skill by model with intervals, breaking-point curves, counterfactual-twin flips, error attribution, real-photo gap, cost. Cut 25 Nov: multi-view consistency, vision gain per model, paired tests | L | Analysis tables and charts generated | done (25 Nov, VTF-07 lane B): breaking-point curves on three axes and counterfactual-twin flips in [findings.md](../reports/v1/findings.md) sections 5 and 6, charts docs/media/breaking_point.png and twin_flip.png; shortcut analysis in P22b |
| P23a | v1.1: fold in the 36 tasks of scenes 0130, 0174 and 0229 | S | — | dropped 25 Nov (VTF-07): v1 stays canonical; the diff (docs/reports/v1/v1_1_check_diff.json) is shown as repeatability evidence |
| P24 | Findings (findings.md and the dataset report) and one risk-card page with a column per model setting (docs/reports/v1/risk_cards.md); no separate findings HTML (cut 25 Nov) | M | Report reads well on its own | done (25 Nov): [risk_cards.md](../reports/v1/risk_cards.md), findings.md and the dataset report's findings section |

## Stage 5: Demo pack

| ID | Item | Size | Done when | Status |
| --- | --- | --- | --- | --- |
| P25 | Task Explorer on Vercel: glTF scene viewer, camera views, question, truth, every model's answer, gate evidence | L | Public URL works on a laptop and a phone | done: v1 with every Claude model and a conflict gallery, https://vtf-task-explorer.vercel.app (re-export when other providers land) |
| P26 | Hugging Face dataset release and data card | S | Public dataset page | done (25 Nov): https://huggingface.co/datasets/Hrishi1802/hrishikesh-visual-task-factory, public, 29,381 files, 2.54 GB, CC0 data card with the frozen hashes and the known material issue (scripts/hf_release.py) |
| P27 | README for judges, quick 20-image recipe (one command), `vtf verify` showcase, VALIDATION / EVAL_PROTOCOL / ARCHITECTURE docs; Dockerfile cut 25 Nov | M | A fresh clone runs the quickstart (docs/reports/quickstart_check.md) | done (25 Nov): `uv run vtf quickstart` (recipes/quick.yaml: 7 scenes, 21 images, 112 tasks in 12 families), fresh clone to verified run in 4 min 15 s; [verify showcase](../reports/v1/verify_showcase.md); docs/VALIDATION.md, EVAL_PROTOCOL.md, ARCHITECTURE.md |
| P28 | Technical slides on the organizers' template, and a demo video script; then record the video; the slide media pack moves to this demo run (25 Nov) | M | Slides and video ready | todo |
| P29 | Submission: every CHECKLIST.md row green, form fields drafted for the founder | S | Founder submits | todo |
