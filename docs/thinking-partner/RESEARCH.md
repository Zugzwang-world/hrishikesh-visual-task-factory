# RESEARCH

Background research for the Visual Task Factory. The first three sections are copied from BRIEF.md (23 Nov 2026). New findings go in the last section, newest first.

## What Innodata actually does (as of Nov 2026)

Innodata reported record Q3 2026 revenue of $95.6M, up 53% year over year, in Rahul Singhal's first quarter as CEO ([8-K](https://www.sec.gov/Archives/edgar/data/0000903651/000110465926123324/tm2628333d1_ex99-1.htm)). The same release says the company believes it is on the cusp of a major market opportunity in physical AI ([press release](https://via.ritzau.dk/pressemeddelelse/15273111/innodata-reports-record-third-quarter-2026-results?publisherId=13560585)).

| Business line | What it is | Hook for our factory |
| --- | --- | --- |
| [Physical AI and computer vision](https://innodata.com/physical-ai/) | Image, video and sensor data for robotics; [data collection](https://innodata.com/physical-ai-data-collection/) for robotics, VLA models, world models and embodied AI; a motion-capture lab opened Sept 2026 that also [independently evaluates](https://www.citybiz.co/?p=911588) humanoids and industrial robots | Our factory evaluates the perception those robots depend on |
| [GenAI Test & Evaluation Platform](https://innodata.com/innodata-unveils-genai-test-evaluation-platform/) | Automated adversarial testing, vulnerability detection and model benchmarking; [risk categories and trend analysis](https://innodata.com/generative-ai/generative-ai-test-evaluation-platform-2025/) | Our report speaks its language: risk categories, trends across difficulty |
| [Model safety and red teaming](https://innodata.com/generativeai/model-safety-evaluation-and-red-teaming/) | Multimodal red teaming and automated benchmarking across text, image and video | Automated multimodal benchmarks with verifiable answers |
| [Agent training and evaluation](https://innodata.com/ai-services-clp/) | RL environments for agent training; an [agent evaluation platform](https://innodata.com/generative-ai/test-and-evaluation-platform/) | Every task has a programmatic answer, so it doubles as a verifiable reward |

## Where we stand against existing work

| Work | What it is | Our difference |
| --- | --- | --- |
| [CLEVR](https://arxiv.org/abs/1612.06890) | 3D-rendered toy shapes with program-generated questions (2017) | Real-world objects, robot cameras, robot-relevant questions |
| [RoboSpatial](https://arxiv.org/abs/2411.16537) | Real 3D scans and egocentric images of indoor and tabletop scenes, for training | Unlimited new scenes with exact answers and controllable difficulty |
| [OmniSpatial](https://arxiv.org/abs/2506.03135) | Spatial-reasoning benchmark; notes the latest reasoning models score 90%+ on existing basic benchmarks | We skip saturated left/right questions and target what robots need exactly |
| [Spatial-DISE](https://arxiv.org/abs/2510.13394) | Spatial VQA built with a Blender pipeline, about 12K pairs | A reusable factory with gates, twins, physical twins and an eval harness, not a fixed set |
| [Embodied3DBench](https://arxiv.org/abs/2605.29074) | Robot-centric benchmark; models relatively strong on high-level object-to-object relations | Exact low-level quantities, clutter and counterfactual twins |

## Sources (from BRIEF.md)

- Hackathon facts: the Visual Task Factory challenge page and Resources tab on the Hack2skill dashboard (read 23 Nov 2026), and the intro-session transcript.
- [Innodata Q3 2026 results (8-K exhibit)](https://www.sec.gov/Archives/edgar/data/0000903651/000110465926123324/tm2628333d1_ex99-1.htm); [Q3 2026 press release](https://via.ritzau.dk/pressemeddelelse/15273111/innodata-reports-record-third-quarter-2026-results?publisherId=13560585)
- [Innodata physical AI](https://innodata.com/physical-ai/); [physical AI data collection](https://innodata.com/physical-ai-data-collection/); [motion-capture lab](https://www.citybiz.co/?p=911588)
- [GenAI Test & Evaluation Platform launch](https://innodata.com/innodata-unveils-genai-test-evaluation-platform/); [platform page](https://innodata.com/generative-ai/generative-ai-test-evaluation-platform-2025/); [model safety and red teaming](https://innodata.com/generativeai/model-safety-evaluation-and-red-teaming/); [AI services](https://innodata.com/ai-services-clp/); [agent evaluation platform](https://innodata.com/generative-ai/test-and-evaluation-platform/)
- [CLEVR](https://arxiv.org/abs/1612.06890); [VQA v2](https://arxiv.org/abs/1612.00837); [Vision language models are blind](https://arxiv.org/abs/2407.06581); [T2ICountBench](https://arxiv.org/abs/2503.06884)
- [RoboSpatial](https://arxiv.org/abs/2411.16537); [OmniSpatial](https://arxiv.org/abs/2506.03135); [Spatial-DISE](https://arxiv.org/abs/2510.13394); [Embodied3DBench](https://arxiv.org/abs/2605.29074)
- [Blender FAQ (licensing of scripts)](https://www.blender.org/support/faq/); [Blender on DGX Spark](https://www.blendernation.com/2026/10/01/blender-5-3-alpha-on-nvidia-dgx-spark-with-dlss-arm64-builds-tutorial-teaser/); [Poly Haven FAQ](https://docs.polyhaven.com/en/faq)
- Fallback family: [FinChart-Bench](https://arxiv.org/abs/2507.14823)
- [Vision Arena leaderboard](https://arena.ai/leaderboard/vision)

## New research

### 23 Nov 2026: Stage 3 scale run, Claude Sonnet 5.5 on 1,500 stratified tasks

- **Data.** out/v1: 300 scenes x 3 cameras (the disk floor stopped the run at 300 of 500), 3,181 tasks after G5 rebalancing; sample stratified by family x rig, seed 0. Settings: adaptive thinking, effort high, structured JSON, prompt 0.2.0, API 2026-10-21. Cost $6.80.
- **Overall:** 928 of 1,500, 61.9% (95% interval 60 to 64%). Front 65.0%, wrist 66.5%, overhead 54.3%.
- **shortcut_conflict (decision 2, the headline diagnostic):** false 369 of 541 (68.2%), true 96 of 213 (45.1%); non-spatial families 62.1%.
  - Nearest to gripper: 59.0% (n 105) vs 15.4% (n 39); 20 of 33 misses are exactly the 2D-shortcut object.
  - Front-to-back: 93.3% vs 67.5%; all 13 misses are the shortcut answer.
  - Free space: 80.6% vs 50.0%; all 21 misses are the shortcut answer.
  - Reachability 62.9% vs 25.0% (n 8); relation 54.2% vs 47.2%; occlusion 46.9% vs 50.0% (n 12).
  - Across spatial families, 65 of 117 misses on conflict tasks equal the 2D-shortcut answer.
- **By family:** front-to-back 86%, count 76%, count by colour 74%, free space 71%, support 67%, gripper view 65%, reachability 60%, containment 60%, relation 51%, occlusion 47%, nearest to gripper 47%, distance in cm 27% (mostly underestimates).
- **G5 blind baseline:** the same model with no image scores 15% (27 of 180) against a 25% prior-only baseline.
- **Reading.** The model reads 3D from 2D proximity cues: when image-plane closeness agrees with 3D truth it does well, and when they disagree it mostly answers what the 2D picture suggests. This is the robot-relevant failure: the wrong grasp target or a missed collision.

### 23 Nov 2026: Nearest-to-gripper re-run with fair evidence (stage 3, Claude Sonnet 5.5)

- **Setup.** 40 stage-2 style scenes (5 to 10 coloured primitives) with the key light, the low gripper (fingertip 3 to 12 cm above the tallest nearby object), and three cameras: front, overhead and wrist. Run `out/nearest_v1`, recipe `recipes/s2_nearest_v1.yaml`, seed 1. 69 tasks passed every gate (G1 to G4 and G7 at 100%).
- **Result.** 47 of 69 correct (68%, 95% interval 58 to 80%), against 5 of 12 (42%) in stage 2.
  - shortcut_conflict false: 47 of 62 (76%).
  - shortcut_conflict true (the object closest to the gripper in the image is not the closest in 3D): 0 of 7, and in all 7 the model named exactly the 2D-shortcut object.
  - By camera: overhead 24 of 31 (77%), front 16 of 23 (70%), wrist 7 of 15 (47%).
- **Reading.** Fair evidence (contact shadows, a low gripper) lifts accuracy, but when image-plane proximity and 3D proximity disagree, the model follows the image every time. Small n (7); the 500-scene run gives the larger split.
- **Same pattern elsewhere, 20-scene check (out/v1check, 290 tasks, all 12 families):** Sonnet 66% overall; front_to_back, free_space and relation conflict tasks are where wrong answers equal the shortcut answer. Weakest families: occlusion 33%, nearest to gripper 41%, containment 47%; strongest: front-to-back 85%, support 81%, count 80%.
- **Validity fixes found by this check, before the scale run:** occlusion questions whose answer was the bowl holding the occluder are now rejected; reach-zone questions need the whole zone in frame; the scorer resolves names among the objects visible in the task's image.

### 23 Nov 2026: First model result (stage 2, Claude Sonnet 5.5)

- 83 gated tasks from 20 front-camera scenes: count 37/37, front-to-back 34/34, nearest to the gripper 5/12 (42%); 92% overall; no parse failures; $0.25 in all.
- Every miss is nearest-to-gripper, and the misses look like a 2D shortcut: the model picks the object that looks closest to the fingertips in the image, not in 3D. In scene 0 the tip hangs 19 cm directly above the black box, but in the image the fingertips sit next to the green cone further back; the model answered "green cone". The ground truth was checked by projecting the tip onto the table.
- This is the robot-relevant error the brief predicted (metric depth near the gripper) and the first evidence for the findings report. 12 tasks is a tiny sample: confirm at scale in stage 4.

### 23 Nov 2026: Blender 5.3 measurements for the renderer (expert subagent)

- Rigid-body settle: Blender's default 10 substeps let cylinders fall through the table; 40 substeps and 20 iterations settled 20 of 20 seeds by frame 57 in about 0.07 s, bit-identical run to run. Convex hulls are 10x slower and let spheres roll.
- Cycles' Z pass is planar camera depth (matched analytic depth to 1e-6 m); background is 1e10. The index and depth passes behave like pixel-centre rays and do not depend on the seed.
- On the Metal GPU, depth differs by about 6e-8 m in 1 or 2 pixels per frame between re-renders; CPU passes are exact and the CPU index equals the GPU index pixel for pixel.
- One-sample CPU amodal masks take about 0.04 s each; visible pixels were inside the mask in 70 of 70 cases.

### 23 Nov 2026: Anthropic Messages API for the eval harness

- `claude-sonnet-5-5` (released 28 Sep 2026) takes image input; $2 / $10 per million input / output tokens.
- API version 2026-10-21 rejects temperature, top_p and top_k with a 400, so repeated runs can differ; record the model ID and API version with every result.
- Structured outputs (`output_config.format` with a JSON schema) work together with image input; confirmed on 83 real calls.
- Sources: [Sonnet 5.5 overview](https://platform.claude.com/docs/en/models/sonnet-5-5/overview), [vision](https://platform.claude.com/docs/en/build-with-claude/vision), [structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs), [versioning](https://platform.claude.com/docs/en/api/versioning), [pricing](https://platform.claude.com/docs/en/about-claude/pricing)

### 23 Nov 2026: Blender 5.3 is the current stable release

- Blender 5.3 was released on 10 Nov 2026. It is a regular release (5.4 replaces it); 5.2 is the current LTS, with fixes until July 2028. Minimum macOS is now 13 Ventura.
- Relevant to us: Cycles adds dispersion to the Principled BSDF; a new Layered Compositor; 5.3 also brings support for NVIDIA RTX Spark devices, which matters for the DGX Spark render plan.
- blender.org/download refuses automated fetches (HTTP 403), so the version was confirmed from release coverage and the Homebrew cask (5.3.0).
- Sources: [Phoronix](https://phoronix.com/news/Blender-5.3-Released); [CG Channel](https://www.cgchannel.com/?p=180007); [Digital Production](https://digitalproduction.com/2026/11/11/blender-5-3-splat-happens-and-everything-else-gets-better/); [5.3 release notes, core](https://developer.blender.org/docs/release_notes/5.3/core/)

### 23 Nov 2026: Claude Code subagent frontmatter

- File-based subagents in `.claude/agents/*.md` accept, among others: `name`, `description`, `tools`, `disallowedTools`, `model`, `permissionMode`, `maxTurns`, `skills`, `memory`, `effort`, `isolation`, `color`. Unknown keys are silently ignored.
- `model` accepts aliases (`opus`, `sonnet`, `haiku`, `fable`), `inherit`, or a full model ID such as `claude-opus-5-5`.
- `effort` accepts `low`, `medium`, `high`, `xhigh`, `max`; available levels depend on the model. The `CLAUDE_CODE_EFFORT_LEVEL` environment variable overrides it.
- `isolation: worktree` runs the subagent in a temporary git worktree, branched from the default branch.
- Source: [Claude Code docs, subagents](https://code.claude.com/docs/en/sub-agents)
