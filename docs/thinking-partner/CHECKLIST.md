# Hackathon checklist

Everything the organizers asked for, with evidence. Tick a row when the evidence exists.

## Official problem statement

| ID | Requirement | Evidence | Done |
| --- | --- | --- | --- |
| C01 | An image family and a visual-understanding problem with real practical impact | BRIEF.md, slides | [ ] |
| C02 | Automated pipeline that generates synthetic images, questions and verifiable reference answers | vtf generate, render, validate: stage 2 run of 20 scenes, 83 tasks ([contact sheet](../media/stage2_contact_sheet.png)); stage 3 run of 300 scenes x 3 cameras, 3,181 tasks ([contact sheet v2](../media/contact_sheet_v2.jpg), [dataset report](../reports/v1/index.html)) | [x] |
| C03 | Explain the application, why errors matter, and how the synthetic examples represent its visual challenges | README, slides, physical-twin results | [ ] |
| C04 | Each task requires visual understanding | G5: Sonnet with no image 15% vs 25% prior-only on the v1 run; rebalanced answer distributions ([dataset report](../reports/v1/index.html)) | [x] |
| C05 | Each image holds enough evidence to answer | G3 99.5% pass on 3,653 v1 candidates; rule A in [spec/families.md](../../spec/families.md); zones fully in frame; scorer names only visible objects ([dataset report](../reports/v1/index.html)) | [x] |
| C06 | A reliable method for checking answers | Typed scorer, EVAL_PROTOCOL.md | [ ] |
| C07 | Reference answers established and validated independently of the evaluated model | G1 100% (programs on the scene file) and G2 95.4% (independent recompute from rendered pixels) on the v1 run; G6 twins 484 of 484 changed answer; no model in any gate ([dataset report](../reports/v1/index.html)); VALIDATION.md still to write | [x] |
| C08 | Frontier models evaluated, strengths and weaknesses reported | Findings report, risk cards. Claude part done: [findings.md](../reports/v1/findings.md) (Haiku, Sonnet, Opus high and xhigh on the same 1,567 tasks); Google, OpenAI and open models and the risk cards to come | [ ] |

## Judging criteria

| ID | Criterion | Evidence | Done |
| --- | --- | --- | --- |
| J1 | Working, repeatable implementation (25%) | vtf verify, seeds, quickstart | [ ] |
| J2 | Validity and validation evidence (25%) | Gate report, founder audit, physical twins | [ ] |
| J3 | Meaningful variation (20%) | Coverage matrix, 12 families, difficulty controls | [ ] |
| J4 | Originality and engineering judgment (15%) | ARCHITECTURE.md, the seven moves in BRIEF.md | [ ] |
| J5 | Practical impact and communication (15%) | Task Explorer, slides, video. Explorer v1 live with every Claude model and a conflict gallery: https://vtf-task-explorer.vercel.app; headline chart for the slides: [docs/media/shortcut_conflict_accuracy.png](../media/shortcut_conflict_accuracy.png) | [ ] |

## Submission form

| ID | Item | Evidence | Done |
| --- | --- | --- | --- |
| S1 | Public GitHub repo link | Repo URL: [Zugzwang-world/hrishikesh-visual-task-factory](https://github.com/Zugzwang-world/hrishikesh-visual-task-factory) | [x] |
| S2 | Documentation | README, ARCHITECTURE, VALIDATION, EVAL_PROTOCOL | [ ] |
| S3 | Working prototype | Quickstart runs; Task Explorer live (https://vtf-task-explorer.vercel.app, v1 with all Claude models, 24 Nov) | [ ] |
| S4 | Demo video link | Video URL | [ ] |
| S5 | Slides on the mandatory template | Slides file | [ ] |
| S6 | Prototype brief and technologies used (form text) | Draft text in DEMO.md | [ ] |
