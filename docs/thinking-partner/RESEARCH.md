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
