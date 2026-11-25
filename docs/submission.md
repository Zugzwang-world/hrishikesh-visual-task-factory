# Submission form text

Draft answers for the hack2skill submission form (brief VTF-09, section 5). Copy from the rendered page, not the raw file: the `<!--ph:...-->` comments around a Phase 2 value are how `scripts/refresh_numbers.py` fills it, and they are invisible when rendered. Anything still shown as {{...}} is filled by Phase 2 or by the founder.

What the organizers' public page says to submit (read 25 Nov 2026, [hack2skill.com/event/innodata](https://hack2skill.com/event/innodata), FAQ): a working solution for the chosen challenge, with source code, setup instructions, sample outputs, validation evidence and a brief technical explanation, through the Participant Dashboard, by **6 December 2026, 11:59 PM IST**. Our notes from the dashboard (23 Nov, BRIEF.md Part 4) add the form fields used below. The public page states no character limits, video length or file format: **all limits here are unverified assumptions** (the video at most 3 minutes; links shared as unlisted YouTube or Google Drive links). See Asks in docs/thinking-partner/NOW.md.

## Team

- Team name: <!--ph:TEAM_NAME-->{{TEAM_NAME}}<!--/ph-->
- Team leader: <!--ph:TEAM_LEADER-->{{TEAM_LEADER}}<!--/ph-->

## Challenge

Challenge 02: Visual Task Factory.

## Prototype brief, short (370 characters)

A factory of 3D robot-workcell vision tasks whose answers are proven from the scene file, never by an AI. Photoreal Blender scenes, three robot cameras, 12 spatial question families, eight model-free gates, and real photos of physical twins. On 3,181 tasks, every Claude model we tested drops about 25 points when the 2D picture misleads, and more thinking doesn't help.

## Prototype brief, long (1,530 characters, 250 words, before the two Phase 2 sentences)

Robots now decide what to grab by asking a vision-language model what it sees, so a spatial mistake becomes a wrong grasp. Visual Task Factory generates tasks that test exactly that, with answers proven without AI. It builds photoreal 3D robot-workcell scenes in Blender (everyday objects on a table, seen by overhead, front and gripper cameras), asks the questions a robot must get right (nearest to the gripper, front to back, occlusion, support, containment, reachability, free space, distance, counts), and computes every answer with a typed program on the scene file. Eight model-free gates check every task: a second computation from the rendered mask and depth pixels, visibility, no near-ties, blind baselines, counterfactual twins (move one object and the answer must change), determinism, and registration of real photos of physical twins on a printed mat. Frozen dataset v1 holds 300 scenes and 3,181 tasks in 12 families. On the same 1,567 tasks, the Claude models we tested (Haiku 5.5, Sonnet 5.5, Opus 5.5 at effort high and xhigh) are about 25 points less accurate when the flat 2D picture misleads; the gap survives matched and regression checks, more thinking does not close it, and they get both versions of a counterfactual twin right only 34 to 52% of the time. <!--ph:AUDIT-->{{AUDIT}}<!--/ph--> <!--ph:SIM2REAL-->{{SIM2REAL}}<!--/ph--> One command rebuilds the tasks from a seed, byte-identical; a 3D Task Explorer, the dataset on Hugging Face, findings with intervals and per-model risk cards come with it. The same factory gives training data and verifiable rewards for robot learning.

## Technologies used

Python 3.12, uv, pydantic, NumPy, OpenCV (including ArUco markers for photo registration), Blender 5.3 (Cycles rendering and rigid-body physics), Poly Haven CC0 3D assets, the Anthropic Claude API (Message Batches), Next.js, React and three.js (react-three-fiber) on Vercel, Hugging Face Datasets, ffmpeg and Playwright for the demo media; built with Claude Code.

## Links

| What | Link |
| --- | --- |
| Public GitHub repo | https://github.com/Zugzwang-world/hrishikesh-visual-task-factory |
| Task Explorer (live prototype) | https://vtf-task-explorer.vercel.app |
| Conflict gallery in the Explorer | https://vtf-task-explorer.vercel.app/?view=gallery |
| Dataset v1 on Hugging Face | https://huggingface.co/datasets/Hrishi1802/hrishikesh-visual-task-factory |
| Findings | https://github.com/Zugzwang-world/hrishikesh-visual-task-factory/blob/main/docs/reports/v1/findings.md |
| Risk cards | https://github.com/Zugzwang-world/hrishikesh-visual-task-factory/blob/main/docs/reports/v1/risk_cards.md |
| Validation evidence | https://github.com/Zugzwang-world/hrishikesh-visual-task-factory/blob/main/docs/VALIDATION.md |
| Setup instructions (README quickstart) | https://github.com/Zugzwang-world/hrishikesh-visual-task-factory#readme |
| Demo video | <!--ph:VIDEO_URL-->{{VIDEO_URL}}<!--/ph--> |
| Slides (organizers' template) | <!--ph:SLIDES_URL-->{{SLIDES_URL}}<!--/ph--> |

The slides are docs/slides/vtf_slides.pptx (PDF beside it). To share them as a link: upload the .pptx to Google Drive, open it with Google Slides, and share "anyone with the link can view".

## Checks before pressing submit

- The repo is public and its main branch has this commit or later.
- The Explorer and the Hugging Face page open in a private browser window.
- The video and slide links open in a private browser window.
- No {{...}} placeholder is left on this page, in the slides or in the video captions (`uv run scripts/refresh_numbers.py --check`).
