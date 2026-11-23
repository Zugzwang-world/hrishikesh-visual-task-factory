# Hackathon checklist

Everything the organizers asked for, with evidence. Tick a row when the evidence exists.

## Official problem statement

| ID | Requirement | Evidence | Done |
| --- | --- | --- | --- |
| C01 | An image family and a visual-understanding problem with real practical impact | BRIEF.md, slides | [ ] |
| C02 | Automated pipeline that generates synthetic images, questions and verifiable reference answers | vtf generate, render, validate: stage 2 run of 20 scenes, 83 tasks ([contact sheet](../media/stage2_contact_sheet.png)) | [x] |
| C03 | Explain the application, why errors matter, and how the synthetic examples represent its visual challenges | README, slides, physical-twin results | [ ] |
| C04 | Each task requires visual understanding | G5 blind-baseline results | [ ] |
| C05 | Each image holds enough evidence to answer | G3 results, rule A | [ ] |
| C06 | A reliable method for checking answers | Typed scorer, EVAL_PROTOCOL.md | [ ] |
| C07 | Reference answers established and validated independently of the evaluated model | G1, G2, VALIDATION.md | [ ] |
| C08 | Frontier models evaluated, strengths and weaknesses reported | Findings report, risk cards | [ ] |

## Judging criteria

| ID | Criterion | Evidence | Done |
| --- | --- | --- | --- |
| J1 | Working, repeatable implementation (25%) | vtf verify, seeds, quickstart | [ ] |
| J2 | Validity and validation evidence (25%) | Gate report, founder audit, physical twins | [ ] |
| J3 | Meaningful variation (20%) | Coverage matrix, 12 families, difficulty controls | [ ] |
| J4 | Originality and engineering judgment (15%) | ARCHITECTURE.md, the seven moves in BRIEF.md | [ ] |
| J5 | Practical impact and communication (15%) | Task Explorer, slides, video | [ ] |

## Submission form

| ID | Item | Evidence | Done |
| --- | --- | --- | --- |
| S1 | Public GitHub repo link | Repo URL: [Zugzwang-world/hrishikesh-visual-task-factory](https://github.com/Zugzwang-world/hrishikesh-visual-task-factory) | [x] |
| S2 | Documentation | README, ARCHITECTURE, VALIDATION, EVAL_PROTOCOL | [ ] |
| S3 | Working prototype | Quickstart runs; Task Explorer live | [ ] |
| S4 | Demo video link | Video URL | [ ] |
| S5 | Slides on the mandatory template | Slides file | [ ] |
| S6 | Prototype brief and technologies used (form text) | Draft text in DEMO.md | [ ] |
