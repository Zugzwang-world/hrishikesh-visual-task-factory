# Photo pipeline proof (before real photos)

The digital-twin renders stand in for phone photos, under anonymous names in a shuffled order, so
the whole path from inbox to paired tasks runs as it will on the founder's photos.

```bash
uv run python scripts/twins_proof_inbox.py out/twins_digital out/twins_proof/inbox
scripts/blender_lock.sh twins_proof -- uv run vtf twin register out/twins_proof/inbox --out out/twins_proof
uv run vtf twin sim-to-real out/twins_proof --model fake-oracle
```

- 39 stand-in photos: the 36 views of the 12 layouts (33 named IMG_xxxx, 3 named L01_<spot>)
  and the 3 views of the misplaced control.
- All 36 correct views: layout and spot recognised correctly (33 of them from the image alone),
  G8 passed, sibling scene written at the photo's estimated pose.
- The 3 misplaced views: rejected (no layout explains the moved onion: best match below the
  recognition margin). Named as L01 photos, G8 rejects them at 39 to 41 mm (g8_digital_check.md).
- Siblings rendered and validated; their settle matches the digital twins' to 0.0 mm.
- 154 real tasks paired one to one with their synthetic siblings, from 11 layouts. L08
  (scene_0027) gives none: the tape roll in the bowl is still creeping at the end of the settle,
  so validate rejects that scene (in the digital twins too).
- Sim-to-real with fake-oracle: photos 100%, renders 100%, difference 0 (95% interval 0 to 0),
  0 discordant pairs: the pairing, scoring and statistics run end to end. Real model numbers
  come from the same command with `--model claude-...` once the photos are in.

Outputs (not in git): out/twins_proof/registration.jsonl (G8 evidence per photo),
overlays/, sim/ (sibling run), real/ (photo tasks, a run `vtf eval` reads), pairs.jsonl,
sim_to_real/summary.md.
