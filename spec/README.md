# spec

Versioned, renderer-independent JSON Schemas. Coming in stage 2:

- `scene.schema.json`: the world state (table, objects with asset ID, pose, scale and material, stacks and containers, gripper pose, zones, cameras, lighting, seed). Every answer is computed from this file.
- `task_package.schema.json`: one task (image, question, answer, evidence, gate report, seed).
