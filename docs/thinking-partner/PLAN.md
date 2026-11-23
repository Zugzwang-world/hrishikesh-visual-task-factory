# Plan

Work items in build order. Status is todo, doing or done. Add, split or drop items whenever it helps; no approval needed. Concept and architecture: BRIEF.md.

## Stage 1: Setup

| ID | Item | Size | Done when | Status |
| --- | --- | --- | --- | --- |
| P01 | Repo, CLAUDE.md, roster, thinking-partner files, Blender smoke render | M | Smoke render and passes exist; repo public | done |
| P02 | Tracker: PLAN.md, CHECKLIST.md, update rule | S | Both files pushed | done |
| P02a | Finish setup: Node 26 LTS, Vercel CLI, Hugging Face CLI and empty dataset repo, plan libraries, Poly Haven proof render, determinism decision, parallel-work lanes | M | Tools proven; Poly Haven render exists; HF dataset repo exists | doing |

## Stage 2: First scene (one task end to end)

| ID | Item | Size | Done when | Status |
| --- | --- | --- | --- | --- |
| P03 | Scene spec v0: pydantic SceneSpec (table, objects with asset ID, pose, scale, material; gripper; zones; cameras; lights; seed) plus JSON Schema export | M | spec/scene.schema.json generated; a sample scene file validates | todo |
| P04 | Sampler v0: 5 to 10 procedural objects (boxes, cylinders, spheres) in distinct colours on a table, no overlaps, seeded | S | Same seed gives a byte-identical scene file | todo |
| P05 | vtf-blender renderer v0: build the scene from the file, rigid-body settle, write settled poses back, render beauty, object-index and depth from one camera | L | One command turns a scene file into PNG, mask, depth and a settled scene file | todo |
| P06 | Question programs v0: count, nearest to gripper, front-to-back; templates with two phrasings each; typed answers | M | Programs return answer plus the object IDs used as evidence | todo |
| P07 | Gates v0 (G1 truth, G3 visibility from masks, G7 determinism) and the task package writer | M | out/run/tasks/ID/ holds image, task.json and gate results | todo |
| P08 | Eval v0: one Anthropic model answers in JSON; typed scorer; SQLite cache | S | Accuracy printed for a small run; reruns hit the cache | todo |
| P09 | First end-to-end run on about 20 tasks, plus a contact sheet (image, question, truth, model answer) saved for DEMO.md | S | Contact sheet exists; NOW.md says stage 2 done | todo |

## Stage 3: Core factory

| ID | Item | Size | Done when | Status |
| --- | --- | --- | --- | --- |
| P10 | Assets: about 40 Poly Haven objects via their API, scale-normalised, real-world sizes in assets/manifest.yaml; procedural scale references; cm-grid table texture | M | Manifest lists every asset; each loads and renders | todo |
| P11 | Sampler v1: clutter levels, stacks, containers, reach zones, look-alikes; difficulty controls in recipes | M | Recipe sliders change scenes as expected | todo |
| P12 | Look: Poly Haven HDRI lighting, PBR materials, denoising, sample count tuned for speed | S | Hero renders saved for DEMO.md | todo |
| P13 | Cameras: overhead, front and gripper rigs with jitter; post-render camera-quality dial (noise, blur, distortion, CCTV look) | M | Same scene renders from all three cameras at three quality levels | todo |
| P14 | All 12 question families from BRIEF.md, each with a twin rule | L | Every family produces valid tasks and twins | todo |
| P15 | Gates G2 (answer recomputed from mask and depth pixels), G4 (no near-ties), G5 (prior-only and question-only baselines), G6 (twins) | L | Gate report shows pass rates and rejection reasons | todo |
| P16 | Audit page: a small local page that shows sampled tasks without answers and records the founder's answers | S | Founder can audit 150 tasks; accuracy computed | todo |
| P17 | Dataset report: gate pass rates, rejection reasons, coverage matrix, answer distributions | M | Static HTML report | todo |
| P18 | Scale run: about 800 scenes and 3,000 tasks (Mac overnight; DGX Spark or cloud GPU if needed) | M | Dataset v1 candidate on disk with its report | todo |

## Physical twins (alongside stages 3 and 4)

| ID | Item | Size | Done when | Status |
| --- | --- | --- | --- | --- |
| P19 | Placement mat PDF (cm grid, ArUco corner markers) and printable layout sheets for about 30 twin scenes built from real-object stand-ins | S | PDFs ready to print | todo |
| P20 | Founder photo session: about 150 photos from marked spots | Founder | Photos in the repo's twin folder | todo |
| P21 | G8: register each photo to its layout; package twin tasks | M | Twin tasks pass G8 and join the dataset | todo |

## Stage 4: Evaluate

| ID | Item | Size | Done when | Status |
| --- | --- | --- | --- | --- |
| P22 | Eval harness v1: adapters for 3 closed and 2 open models (IDs verified on the web), async, rate limits, cost pilot | M | Pilot cost per task known | todo |
| P23 | Full evaluation and analyses: skill by model with intervals, breaking-point curves, real-photo gap, multi-view consistency, twins, vision gain, error attribution, paired tests, cost | L | Analysis tables and charts generated | todo |
| P24 | Findings report and one risk card per model (static HTML) | M | Report reads well on its own | todo |

## Stage 5: Demo pack

| ID | Item | Size | Done when | Status |
| --- | --- | --- | --- | --- |
| P25 | Task Explorer on Vercel: glTF scene viewer, camera views, question, truth, every model's answer, gate evidence | L | Public URL works on a laptop and a phone | todo |
| P26 | Hugging Face dataset release and data card | S | Public dataset page | todo |
| P27 | README quickstart, Dockerfile, quick 20-image recipe, `vtf verify` showcase | M | A fresh clone runs the quickstart | todo |
| P28 | Technical slides on the organizers' template, and a demo video script; then record the video | M | Slides and video ready | todo |
| P29 | Submission: every CHECKLIST.md row green, form fields drafted for the founder | S | Founder submits | todo |
