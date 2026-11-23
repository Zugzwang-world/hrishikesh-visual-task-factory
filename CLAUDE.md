# CLAUDE.md: operating rules for Claude Code

## Purpose

This repo is the Visual Task Factory for the Innodata AI Hackathon (Visual Task Factory track). It generates photoreal 3D robot-workcell scenes in Blender (a table of everyday objects seen through overhead, front and gripper cameras), asks the spatial questions a robot must get right (counts, nearest to gripper, depth order, support, containment, occlusion, reachability), proves every answer from the scene file with model-free checks, and evaluates frontier vision models on the result. The full concept, scoring and build order are in [docs/thinking-partner/BRIEF.md](docs/thinking-partner/BRIEF.md). Read it before any non-trivial work.

## Working style

- This is a prototype, not production. No security, compliance, licensing or production hardening.
- No tests, reviews or gates beyond what the demo or the judging needs. The judges score repeatability and validation evidence, so the validation gates (G1–G8) and determinism checks are product, not overhead.
- If an instruction doesn't fit reality, pick the sensible option, keep moving, and log it in `docs/thinking-partner/DECISIONS.md`.
- Build directly, fix what breaks, and spend effort only where the judges score or the demo shows.

## Architecture rules

- **The scene file is the truth.** A renderer-independent JSON scene spec holds the full world state. Images and questions are both derived from it.
- **Answers come only from programs run on the scene file.** Never from a model, never typed by hand.
- **No AI image generator anywhere in the image path.** Blender only.
- **The `vtf` core package never imports `bpy`.** `vtf-blender` runs inside Blender and talks to the core only through files (scene JSON in; images, masks, depth and settled poses out).
- **Every generator is seeded.** Everything is a pure function of pack version, seed and parameters, so a task ID is a content hash.
- **Generated output goes under `out/`**, which is gitignored. Large releases go to Hugging Face, not git.
- Engine code never contains domain words; domain packs never contain pipeline logic.
- Formats are versioned.

## Roster

| Role | Model | Effort | Where | Job |
| --- | --- | --- | --- | --- |
| Coordinator | Opus 5.5 (`claude-opus-5-5`) | xhigh | Main session | Plans, dispatches, merges, keeps the thinking-partner files current |
| builder | Opus 5.5 | xhigh | `.claude/agents/builder.md`, own git worktree | Parallel M and L implementation work |
| expert | Opus 5.5 | xhigh | `.claude/agents/expert.md` | Blender, rendering, physics settle, camera simulation and evaluation-methodology questions |
| reviewer | Opus 5.5 | xhigh | `.claude/agents/reviewer.md` | Quick pass before merging L work |
| scout | Haiku 5.5 (`claude-haiku-5-5`) | xhigh (unverified for Haiku) | `.claude/agents/scout.md` | Web lookups: versions, model IDs, docs |
| advisor | Opus 5.5 | — | Session setting, not a subagent file | Second opinion on hard calls (exact mechanism unverified) |

Frontmatter keys (`name`, `description`, `tools`, `model`, `effort`, `isolation`, `color`) were checked against the Claude Code subagent docs on 23 Nov 2026. The docs say available effort levels depend on the model, so `xhigh` on Haiku is unverified. `CLAUDE_CODE_EFFORT_LEVEL` overrides the `effort` field if set.

## Sizing

- **S**: one file or a small fix; the Coordinator does it directly.
- **M**: a module or a feature across a few files; a builder in a worktree, or the Coordinator if nothing runs in parallel.
- **L**: a cross-cutting feature or a new subsystem; a builder in a worktree, then a reviewer pass before merge.

## Git

- `main` is always runnable.
- Parallel work happens in git worktrees (builders use `isolation: worktree`).
- Merge when it runs. Push after each merge.
- Small logical commits with clear messages.

## Parallel work

Contracts land on main first: spec/scene.schema.json and spec/task_package.schema.json (P03). After that, lanes run in parallel worktrees, and each lane edits only its own folders:

| Lane | Folders |
| --- | --- |
| Contracts (Coordinator) | packages/vtf/src/vtf/core; spec/*.schema.json; spec/examples/; packages/vtf/src/vtf/cli.py (each lane may edit only its own command function) |
| Spec and sampler | packages/vtf/src/vtf/scene; recipes/ |
| Render | packages/vtf-blender; packages/vtf/src/vtf/render (the bridge that calls Blender); packages/vtf/src/vtf/camera; assets/ |
| Questions | packages/vtf/src/vtf/programs |
| Validation and packaging | packages/vtf/src/vtf/validate, package |
| Eval | packages/vtf/src/vtf/eval; configs/models.yaml |
| Twins | packages/vtf/src/vtf/twin |
| Web | web/ |

Only the Coordinator edits docs/thinking-partner/ and CLAUDE.md, after merges. A lane that needs a contract change stops and asks the Coordinator, who changes the schema on main first. Merge a lane when its piece runs; integrate on main.

## Thinking-partner habit

The claude.ai Project syncs `docs/thinking-partner/` and this file from GitHub, and the thinking partner reads them. At the end of every work session:

1. Rewrite `docs/thinking-partner/NOW.md`: timestamp, current stage, what exists, what is next, blockers.
2. Update `DECISIONS.md`, `OPEN.md` or `DEMO.md` when they changed; add new findings to `RESEARCH.md`.
3. Commit and push, so the Project sync stays fresh.

## Tracker

docs/thinking-partner/PLAN.md and CHECKLIST.md are the tracker. Mark a PLAN item doing when you start it and done when it is merged. Tick a CHECKLIST row as soon as its evidence exists, and link the evidence. Rewrite NOW.md, then push, after every merge and at the end of every session. Add, split or drop PLAN items whenever it helps.

## Founder communication

- The founder is the only human. He works by voice, does no coding and no clerical work, and supervises.
- Reports are short and voice-friendly: plain sentences, no tables or code unless asked.
- Ask him only for what you cannot do: logins, API keys, browser approvals, physical photos and objects, the blind audit. Make each ask one line.

## Verify before relying

Check versions, model names and APIs on the web (or with the scout) before relying on them. Mark anything you could not confirm as unverified.

## Commands

```bash
uv sync                              # install the workspace
uv run vtf --help                    # CLI: generate, render, validate, eval, verify
uv run python -m vtf.core.schemas    # regenerate spec/*.schema.json from the pydantic contracts
uv run python spec/examples/make_fixtures.py   # regenerate the contract fixtures (no Blender)
uv run pytest                        # tests
uv run ruff check .                  # lint
/Applications/Blender.app/Contents/MacOS/Blender -b -P packages/vtf-blender/scripts/smoke_render.py   # smoke render into out/smoke/
/Applications/Blender.app/Contents/MacOS/Blender -b -P packages/vtf-blender/scripts/smoke_render.py -- --device CPU   # byte-identical images
uv run python assets/fetch_polyhaven.py wooden_bowl_02 --kind model   # cache a Poly Haven asset in assets/cache/
/Applications/Blender.app/Contents/MacOS/Blender -b -P packages/vtf-blender/scripts/polyhaven_smoke.py   # out/smoke/polyhaven.png
```
