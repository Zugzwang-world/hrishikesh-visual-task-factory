# NOW

**Updated:** 23 Nov 2026, 17:15 IST
**Stage:** Setup is **complete** (P01, P02 and P02a done). Next item: **P03, scene spec v0**, which starts stage 2, First scene.
**Deadline:** submissions close 6 Dec; our real deadline is the night of 5 Dec.

## What exists

- Public repo [Zugzwang-world/hrishikesh-visual-task-factory](https://github.com/Zugzwang-world/hrishikesh-visual-task-factory), `main` pushed, CI green (`uv sync`, ruff, pytest).
- `CLAUDE.md` with operating rules, the tracker rule and the new **Parallel work** lanes; four subagents in `.claude/agents/`.
- This folder: BRIEF, NOW, OPEN, DECISIONS, DEMO, RESEARCH, PLAN and CHECKLIST.
- uv workspace on Python 3.12. `packages/vtf` now carries the plan's libraries (pydantic, typer, numpy, OpenCV headless, pillow, pyyaml, jinja2, httpx, scikit-image, python-dotenv); hypothesis and pytest are in the dev group.
- `packages/vtf-blender`: a shared GPU/CPU device module, the smoke render and the Poly Haven proof render.
- Local `.env` (gitignored) with empty key entries.

## Tools and accounts

| Tool | Version | Account | State |
| --- | --- | --- | --- |
| Blender | 5.3.0 | — | Renders headless on the Metal GPU |
| uv / Python | 0.13.1 / 3.12 | — | Workspace synced |
| GitHub CLI | 2.103.3 | Zugzwang-world | Logged in; repo public |
| Node.js / npm | 26.13.1 LTS / 11.20.0 (pinned in `.node-version`) | — | Ready for the Task Explorer (P25) |
| Vercel CLI | 66.6.0 | zugzwang-world | Logged in; no project yet (P25) |
| Hugging Face CLI | `hf` 2.4.0 | Hrishi1802 | Logged in (write token in `.env` and the hf cache); empty public dataset repo [Hrishi1802/hrishikesh-visual-task-factory](https://huggingface.co/datasets/Hrishi1802/hrishikesh-visual-task-factory) |
| Poly Haven API | public, no key | — | Proven: one model and one HDRI fetched and rendered |

## Poly Haven proof

`out/smoke/polyhaven.png`: Poly Haven's Wooden Bowl 02 (glTF, 1k textures) lit by the studio_small_09 HDRI, Cycles on the Metal GPU, 800x600, 128 samples with denoising, 7.9 s warm. The imported bowl measures 130.7 x 124.5 x 73.1 mm, matching Poly Haven's published size, so real-world scale survives import.

## Determinism: decided

G7 checks scene files, answers, masks and depth exactly; images pass at SSIM 0.999 or above; GPU is the default and a CPU device option gives byte-identical images (for `vtf verify` on stage). BRIEF.md rule C and the G7 row are updated; OPEN item 6 is closed.

## What is next

1. **P03:** scene spec v0, a pydantic SceneSpec with JSON Schema export to `spec/scene.schema.json`. It lands on main first; the parallel lanes start after it.
2. Then P04 to P09, as listed in PLAN.md.

## Blockers and asks

- **Founder:** add the GitHub sync in the claude.ai Project (this repo, `docs/thinking-partner` plus `CLAUDE.md`) and tap Sync now, if not done yet.
- The Anthropic API key is needed at P08, not before.
