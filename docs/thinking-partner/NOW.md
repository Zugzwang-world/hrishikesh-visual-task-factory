# NOW

**Updated:** 23 Nov 2026, 16:50 IST
**Stage:** Stage 1, Setup, is **done** (P01 and P02 in PLAN.md). Next is stage 2, First scene, starting with **P03: scene spec v0**.
**Deadline:** submissions close 6 Dec; our real deadline is the night of 5 Dec.

## What exists

- Public repo [Zugzwang-world/hrishikesh-visual-task-factory](https://github.com/Zugzwang-world/hrishikesh-visual-task-factory), `main` pushed, CI runs `uv sync`, ruff and pytest.
- `CLAUDE.md` with the operating rules, and four subagents in `.claude/agents/`: builder (Opus 5.5, own worktree), expert (Opus 5.5), reviewer (Opus 5.5), scout (Haiku 5.5).
- This folder: BRIEF (verbatim), NOW, OPEN, DECISIONS, DEMO, RESEARCH, plus the tracker: PLAN (work items P01 to P29) and CHECKLIST (every organizer requirement with its evidence; S1 ticked).
- uv workspace on Python 3.12: `packages/vtf` (empty module tree from the brief, Typer CLI with seven stub commands) and `packages/vtf-blender` (scripts folder). Placeholders for `spec/`, `recipes/`, `configs/models.yaml` (model IDs unverified), `assets/manifest.yaml` and `web/`. Three smoke tests pass.
- Blender 5.3.0 installed. Headless Cycles renders on the Metal GPU: about 1 s per 640x480 image at 64 samples once kernels are compiled, 5x faster than the CPU. The smoke render writes beauty, object-index and depth passes to `out/smoke/`.

## What is next: P03, then the rest of stage 2

Stage 2 is done when one task runs end to end. Items are in PLAN.md:

- **P03 (next):** scene spec v0, a pydantic SceneSpec with JSON Schema export.
- P04 sampler v0, P05 Blender renderer v0, P06 question programs v0, P07 gates v0 and the task package writer, P08 eval v0, P09 first end-to-end run of about 20 tasks with a contact sheet.
- Settle the GPU determinism question (OPEN.md item 6) during P05 and P07.

## Blockers and asks

- No blockers.
- Founder: add the GitHub sync in the claude.ai Project (this repo, the `docs/thinking-partner` folder plus `CLAUDE.md`) and tap Sync now.
- API keys go in a local `.env` (names in `.env.example`) before the first model call in stage 2.
