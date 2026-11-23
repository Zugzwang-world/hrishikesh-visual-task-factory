# OPEN

Open questions, each with a recommendation. Close an item by moving its answer to DECISIONS.md.

| # | Question | Recommendation | Status |
| --- | --- | --- | --- |
| 1 | Which exact model IDs do we evaluate (three closed flagships from Anthropic, Google and OpenAI, plus two open models such as a Qwen VL model)? | Verify on the web on the day the evaluation stage starts (Vision Arena leaderboard plus each provider's model list), pin the IDs in `configs/models.yaml`, and record them here. Until then every entry is marked unverified. | Open until stage 4 |
| 2 | How fast does Blender render on this Mac (Apple M4, 10-core GPU)? Is it fast enough for about 800 scenes x 3 cameras? | Measured by the stage-1 smoke render (see below). Re-measure in stage 2 on a real workcell scene with Poly Haven assets, since that is far heavier. If a full scene takes more than about 30 s per camera, drop samples and use the denoiser, render overnight, or move volume to the DGX Spark or a cloud GPU. | Partly answered |
| 3 | Does Metal GPU rendering work headless in Blender on this Mac? | Confirmed or refuted by the stage-1 smoke render (see below). If Metal fails, fall back to CPU and plan volume rendering elsewhere. | See below |
| 4 | Expected task count and required package format from the organizers. | Raise later through their channels (brief Part 4). Keep our versioned spec plus a converter. | Open |
| 5 | Real objects for physical twins: buy or borrow? | Common kitchen items that match Poly Haven assets. | Open |

## Smoke render results (23 Nov 2026)

Filled in after the stage-1 smoke render runs.
