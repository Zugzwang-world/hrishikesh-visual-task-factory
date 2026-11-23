# DECISIONS

A dated log, newest first. Each entry says what was decided and why. Reversals get a new entry rather than an edit.

## 23 Nov 2026: Finish setup (brief 03)

| Decision | Choice | Why |
| --- | --- | --- |
| **Determinism (closes OPEN item 6)** | G7 checks scene files, answers, masks and depth for exact equality, comparing decoded pixels rather than file bytes. Images are compared with SSIM and pass at 0.999 or above. GPU stays the default device. A CPU device option gives byte-identical images when they are wanted, for example `vtf verify` on stage. | The setup run found same-seed GPU renders differ by one colour level in 2 of 307,200 pixels, while masks, depth and CPU renders are exact. Masks, depth and the scene file carry all the truth, and G2 recomputes answers from them. Rule C, the G7 row and the "Repeatable" requirement row in BRIEF.md now say exactly this. |
| Device option | `vtf_blender.device.configure_device("GPU" or "CPU")`, shared by every Blender script; `--device` on the smoke scripts and on the `vtf render` and `vtf verify` stubs | One implementation of the GPU/CPU choice. It tries Metal, then OptiX, CUDA, HIP and oneAPI, so the same code runs on the DGX Spark or a cloud GPU. |
| Node.js | Node 26 LTS (26.13.1) installed with fnm and pinned for this repo in `.node-version`; the machine default stays Node 24 | Node 26 is the active LTS since Oct 2026 and Node 24 is in maintenance. fnm switches on `cd`, so other projects on this Mac are untouched. |
| Vercel CLI | 66.6.0, installed under both Node 24 and Node 26; already logged in as zugzwang-world | No new login was needed. The web app itself waits for P25. |
| Hugging Face CLI | `hf` 2.4.0 from `uv tool install huggingface_hub` | The current CLI is `hf`, which replaced `huggingface-cli`. Dataset repos are created with `hf repos create <id> --type dataset --public`. |
| Python libraries | Runtime libraries in `packages/vtf`: pydantic, typer, numpy, opencv-python-headless, pillow, pyyaml, jinja2, httpx, scikit-image, python-dotenv. hypothesis and pytest sit in the workspace dev group. | The headless OpenCV build has the same API, ArUco included, without GUI libraries, so it suits Docker and CI. Test tools stay out of the runtime package. python-dotenv loads `.env`. `vtf-blender` still imports only what Blender ships (bpy, numpy, OpenImageIO). |
| Poly Haven access | `assets/fetch_polyhaven.py` (stdlib only) caches assets in `assets/cache/` (gitignored). Every API call sends the User-Agent `vtf-visual-task-factory/0.0.1`. Models are fetched as glTF. | The API terms require a unique User-Agent naming the software. glTF avoids `.blend` files, which can carry embedded Python. The assets are CC0. If the Task Explorer ever pulls content from the live API, it must show a "Powered by Poly Haven" credit; our renders of downloaded assets need none. |

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
