# recipes

YAML recipes that drive generation: objects, clutter, cameras, lighting, question mix, size and seed. A recipe with `sampler: v1` runs sampler v1 (`vtf.scene.sampler_v1`); any other runs sampler v0, unchanged.

- `first_scene.yaml`: stage 2, sampler v0. 5 to 10 coloured primitives, one front camera.
- `v1.yaml`: stage 3 main recipe, sampler v1. Manifest assets, clutter 3 to 25, stacks, containers, reach zones, look-alikes, duplicates, the low gripper, front, overhead and gripper cameras with quality levels, four lighting conditions, cm grid on about 40% of scenes, every question family.
- `s2_nearest_v1.yaml`: sampler v1 with stage-2 primitives, the low gripper, three cameras and `nearest_to_gripper` only.

Still to come: `demo.yaml`, `v1_release.yaml`, `physical_twin.yaml`.

Preview a run without Blender: `uv run python -m vtf.scene.preview out/v1dev --out out/v1dev/preview.jpg`.
