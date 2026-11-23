# NOW

**Updated:** 23 Nov 2026, 18:15 IST
**Stage:** **Stage 2, First scene, is done** (P03 to P09). Next: **stage 3, Core factory**, starting with P10 (assets) and P11 (sampler v1).
**Deadline:** submissions close 6 Dec; our real deadline is the night of 5 Dec.

## What exists

- Factory runs end to end on main: `uv run vtf generate`, `render`, `validate`, `eval`, `report` and `verify`. The commands are in CLAUDE.md.
- **Contracts (P03):** pydantic SceneSpec, RenderManifest and TaskPackage in `packages/vtf/src/vtf/core/`. JSON Schemas are in `spec/`. A complete tiny fixture run in `spec/examples/`, made without Blender by an exact numpy ray caster.
- **Sampler (P04):** `recipes/first_scene.yaml` gives 5 to 10 coloured primitives with unique names, a two-finger gripper and one front camera at 1024x768. The same seed gives byte-identical scene files.
- **Renderer (P05):** `packages/vtf-blender/scripts/render_scene.py` plus the `vtf render` bridge.
  - Rigid-body settle writes `scene.settled.json`.
  - Beauty renders on the GPU.
  - The 16-bit index, planar depth and per-object amodal masks come from CPU passes, which are exact on re-render.
- **Questions (P06):** count (all, by colour), nearest to gripper and front-to-back, each with two phrasings and a trace. Near-ties are rejected. There are Hypothesis property tests.
- **Gates and packaging (P07):**
  - G1 re-runs the program; G3 is rule A from the index and amodal masks; G7 records hashes and re-samples the scene file.
  - Also: a settle check, and a render-versus-scene agreement check against the ray caster.
  - Output: `tasks/<id>/{image.png, task.json}`, `manifest.jsonl` and `rejected.jsonl`.
  - `vtf verify` re-renders and compares.
- **Eval (P08):** Anthropic adapter on the official SDK (structured JSON output, no temperature). A typed scorer shares the normaliser with the programs. A SQLite cache makes reruns free.
- 123 tests pass; ruff is clean.

## First end-to-end run (P09): 20 scenes, out/s2

- **Tasks:** 110 candidate questions, 83 accepted (2 to 5 per scene, 18 scenes with 3 or more).
- **Gate pass rates:** G1 88/88 (100%), G3 83/88 (94%), G7 88/88 (100%).
- **Rejection reasons (27):**
  - near-tie, gripper distance under 3 cm: 8
  - near-tie, camera depth under 4 cm: 8
  - near-tie, straight-line distance: 6
  - evidence not visible enough (rule A): 5
  - No settle failures.
- **Render vs scene file:** camera matrices agree in all 20 scenes. Per-object IoU between Blender's index pass and the exact ray cast is at least 0.991, median 0.998. All 20 scene files re-sample byte-identically from their seeds.
- **vtf verify, 5 scenes:** ALL MATCH. Scene files, settled scenes, index, depth, masks, answers and task hashes are identical; beauty SSIM is 0.999999.
- **Render time:**
  - 21 s per scene with the machine heavily loaded (load average about 30); beauty alone took 17 s.
  - The builder measured 10 to 14 s per scene on a quiet machine.
  - Either way, 800 scenes x 3 cameras is an overnight run (OPEN item 2).
- **Claude Sonnet 5.5:**
  - count 37/37, front-to-back 34/34, nearest to gripper 5/12 (42%), overall 76/83 (92%)
  - no parse failures; $0.25; a rerun is fully cached
  - Every miss is a 2D-proximity shortcut near the gripper (see RESEARCH.md).
- **Demo media:** contact sheet `docs/media/stage2_contact_sheet.png`; hero renders `docs/media/hero_scene_0000_front.png` and `hero_scene_0013_front.png`.

## What is next

1. **P10:** Poly Haven assets with real sizes in `assets/manifest.yaml`. The renderer and ray caster handle primitives only so far.
2. **P11:** sampler v1 with clutter, stacks, containers, reach zones and look-alikes. The settle gap must then be measured to whatever an object rests on (review note).
3. **P13:** overhead and gripper cameras; **P14:** the remaining question families; **P15:** G2 (the ray caster is a head start), G4, G5 and G6.

## Blockers and asks

- None blocking.
- The Anthropic API key and the Hugging Face token were both pasted in chat. Rotate them after the hackathon.
- Keys for Google, OpenAI and OpenRouter are needed at stage 4 (P22), not before.
