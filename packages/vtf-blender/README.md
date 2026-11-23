# vtf-blender

Code that runs inside Blender's bundled Python (it imports `bpy`). It reads a scene file written by `vtf`, builds and settles the scene, renders it, and writes images, instance-mask and depth passes, and settled poses back to disk. It never imports `vtf`, and `vtf` never imports it: the two talk only through files.

`scripts/` holds entry points run with Blender in background mode:

```bash
/Applications/Blender.app/Contents/MacOS/Blender -b -P packages/vtf-blender/scripts/smoke_render.py
```
