# NOW

**Updated:** 23 Nov 2026, 16:35 IST
**Stage:** Stage 1, Setup, is **done**. Next is stage 2, First scene.
**Deadline:** submissions close 6 Dec; our real deadline is the night of 5 Dec.

## What exists

- Public repo [Zugzwang-world/hrishikesh-visual-task-factory](https://github.com/Zugzwang-world/hrishikesh-visual-task-factory), `main` pushed, CI runs `uv sync`, ruff and pytest.
- `CLAUDE.md` with the operating rules, and four subagents in `.claude/agents/`: builder (Opus 5.5, own worktree), expert (Opus 5.5), reviewer (Opus 5.5), scout (Haiku 5.5).
- This folder: BRIEF (verbatim), NOW, OPEN, DECISIONS, DEMO, RESEARCH.
- uv workspace on Python 3.12: `packages/vtf` (empty module tree from the brief, Typer CLI with seven stub commands) and `packages/vtf-blender` (scripts folder). Placeholders for `spec/`, `recipes/`, `configs/models.yaml` (model IDs unverified), `assets/manifest.yaml` and `web/`. Three smoke tests pass.
- Blender 5.3.0 installed. Headless Cycles renders on the Metal GPU: about 1 s per 640x480 image at 64 samples once kernels are compiled, 5x faster than the CPU. The smoke render writes beauty, object-index and depth passes to `out/smoke/`.

## What is next: stage 2, First scene

Done when one task runs end to end.

1. Scene spec v0 (pydantic, JSON Schema export) and a seeded sampler for 5–10 objects on a table.
2. `vtf-blender` scene builder that reads the scene JSON, renders with mask and depth passes, and writes them back as files.
3. Three question families: count, nearest to gripper, front-to-back order.
4. G1, G3 and G7 running; one model answers one task.
5. Decide the GPU determinism question (OPEN.md item 6) and re-measure render time on a real scene.

## Blockers and asks

- No blockers.
- Founder: add the GitHub sync in the claude.ai Project (this repo, the `docs/thinking-partner` folder plus `CLAUDE.md`) and tap Sync now.
- API keys go in a local `.env` (names in `.env.example`) before the first model call in stage 2.
