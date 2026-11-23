# DECISIONS

A dated log, newest first. Each entry says what was decided and why. Reversals get a new entry rather than an edit.

## 23 Nov 2026: Setup (stage 1)

| Decision | Choice | Why |
| --- | --- | --- |
| Blender version | **Blender 5.3.0**, installed with the Homebrew cask (`brew install --cask blender`) | Latest stable, released 10 Nov 2026. 5.2 is the LTS; we take 5.3 because the brief says latest stable and 5.3 adds RTX Spark support for the DGX Spark plan. Pin this version in Docker and CI later; images are only byte-identical on the same Blender version (Rule C). |
| GitHub account | Repo lives at `Zugzwang-world/hrishikesh-visual-task-factory`, public, default branch `main` | That is the account `gh` is logged in as on this Mac. |
| Commit email | This repo's git config uses the account's GitHub no-reply address | The GitHub account blocks pushes that expose its private email. Worktrees share the setting. |
| CI action pins | `actions/checkout@v7`, `astral-sh/setup-uv@v10.4.0` | setup-uv publishes only full version tags, so a bare `@v10` fails. |
| Python | 3.12, pinned through uv (`.python-version` and `requires-python`) | As the brief's stack table says. Blender ships its own Python; the `vtf` core never runs inside it. |
| Workspace layout | uv workspace with two members: `packages/vtf` (core, CLI) and `packages/vtf-blender` (scripts run by Blender) | Keeps `bpy` out of the core. The renderer talks to the core only through files. |
| CLI framework | Typer | The setup brief names it. |
| Lint | Ruff, configured in the root `pyproject.toml` | Fast, one tool; no gating beyond what CI runs. |
| CI | One GitHub Action: `uv sync` then `pytest` | Minimal proof that main runs. Determinism checks join it in stage 2. |
| Smoke render | Cycles on the Metal GPU, 64 samples, 640x480, beauty + object-index + depth passes | Proves the headless render path and the passes the gates need. Metal works; results are in OPEN.md. |
| Passes without the compositor | Render to a multilayer EXR and read the passes with Blender's bundled OpenImageIO and numpy | The compositor API changed in Blender 5.x; the EXR route keeps exact float depth and integer object index for G2 with no node setup. |
| No volatile metadata in outputs | Turn off every `use_stamp*` option and re-encode the beauty PNG without Cycles' render-time text chunks | So identical pixels give identical bytes; needed for G7. |
| Two Pythons | The workspace runs on Python 3.12; Blender 5.3 bundles Python 3.13, which runs everything in `packages/vtf-blender/scripts/` | Blender's Python can't be changed. Renderer scripts must stay compatible with Blender's Python and import nothing from `vtf`. |
| Subagent definitions | `.claude/agents/` with builder, expert, reviewer and scout, using the documented frontmatter keys (`model`, `effort`, `isolation`) | Checked against the Claude Code subagent docs on 23 Nov 2026; see RESEARCH.md. |

## 23 Nov 2026: Decided in BRIEF.md

| Decision | Choice |
| --- | --- |
| Image family | Robot workcell: a tabletop seen through robot cameras, rendered in 3D |
| Image source | Blender only; no AI image generators anywhere in the image path |
| Rendering | Mac first; DGX Spark or a cloud GPU when volume needs it |
| Model access | Own API keys |
| Working style | Prototype and flexible: no licence, compliance or production work; no test gates beyond the checks the judges score |
| Organizer contact | None for now; questions later through their channels |
| Repo | hrishikesh-visual-task-factory, public from day one |
| Team | Founder plus Claude Code; no other humans |
| Task Explorer | Built and hosted on Vercel |
| Pitch | Technical and explicit: beyond 2D, exact 3D truth, robot perception for physical AI |
| Fallback | Financial price charts, only if 3D blocks us |
