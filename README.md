# Visual Task Factory: 3D robot workcell

A factory of visual tasks for robot perception. It generates photoreal tabletop scenes in Blender, seen through a robot's overhead, front and gripper cameras, asks the spatial questions a robot must get right (what is nearest the gripper, what is reachable, how hidden something is, what is stacked on what, how many in clutter), and proves every answer from the 3D scene file with checks that never ask an AI model. It then runs frontier vision models on the tasks and reports where they break. Because the truth comes from an exact 3D world state rather than an AI image generator, every depth, distance, occlusion and count is known exactly, and every task can be rebuilt byte for byte from its seed.

Built for the Innodata AI Hackathon, Visual Task Factory track.

> **Work in progress.** Stage 1 (setup) is done: the workspace skeleton, the CLI stubs and a headless Blender smoke render. Scene generation, question programs, validation gates and evaluation arrive in stages 2 to 4. See [docs/thinking-partner/NOW.md](docs/thinking-partner/NOW.md) for current status and [docs/thinking-partner/BRIEF.md](docs/thinking-partner/BRIEF.md) for the full design.

## Quickstart (placeholder)

Requirements: [uv](https://docs.astral.sh/uv/) and Blender 5.3 (`brew install --cask blender` on macOS).

```bash
uv sync
uv run vtf --help
uv run pytest
```

The full pipeline will run like this once stages 2 to 4 land (every command is a stub today):

```bash
uv run vtf generate --recipe recipes/demo.yaml --out out/demo
uv run vtf render out/demo
uv run vtf validate out/demo
uv run vtf eval out/demo --models configs/models.yaml
uv run vtf report out/demo
uv run vtf verify out/demo --sample 20
```

Smoke-test the renderer (writes a beauty image, an object-index pass and a depth pass to `out/smoke/`):

```bash
/Applications/Blender.app/Contents/MacOS/Blender -b -P packages/vtf-blender/scripts/smoke_render.py
```

## Layout

| Path | What it holds |
| --- | --- |
| `packages/vtf/` | Core package and `vtf` CLI. Never imports `bpy`. |
| `packages/vtf-blender/` | Code that runs inside Blender; talks to the core only through files |
| `spec/` | Versioned, renderer-independent JSON Schemas |
| `recipes/` | YAML recipes: objects, clutter, cameras, lighting, question mix, seed |
| `configs/models.yaml` | Models for the evaluation harness |
| `assets/manifest.yaml` | Object IDs, sources and real-world sizes |
| `web/` | Task Explorer (stage 5) |
| `docs/thinking-partner/` | Brief, current status, decisions, open questions, demo plan, research |
