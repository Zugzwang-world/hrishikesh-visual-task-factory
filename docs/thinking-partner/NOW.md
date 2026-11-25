# NOW

**Updated:** 25 Nov 2026, 21:04 IST (end of brief VTF-09, unattended)
**Stage:** **Stage 5, demo pack, on frozen v1.** VTF-09 is done: the slides on the organizers' template, the video kit, the media pack, the form text and the judge Q&A, all with placeholders for the three results we do not have yet. **VTF-08 Phase 2 can start** when the founder says the audit and photos are ready; it ends by filling those placeholders with one command.
**Deadline:** submissions close 6 Dec, 11:59 PM IST (organizers' page); our real deadline is the night of 5 Dec. The organizers' Ask Me Anything session is on 2 Dec.

## Decisions in force (founder, 25 Nov)

- Claude models only: Haiku 5.5 high, Sonnet 5.5 high, Opus 5.5 high and xhigh. Never Fable or Mythos. Findings speak only about the Claude models we tested.
- v1 is canonical: 300 scenes, 3,181 tasks. Its four frozen hashes are unchanged. Programs 0.3.0 changes new runs only.
- out/v1/audit is not touched while the founder audits.
- The video follows [docs/DEMO_STORY.md](../DEMO_STORY.md): the founder on camera at the desk with the physical set, opening and closing there.

## What VTF-09 added (branch claude/vtf-09-demo-run-00c0e1, fast-forwarded onto main)

- **Slides:** [docs/slides/vtf_slides.pptx](../slides/vtf_slides.pptx) and [PDF](../slides/vtf_slides.pdf). 12 slides in the story's order on the organizers' exported template, plus its thank-you card. Every v1 number is read from findings.json. Orange placeholders: the audit, the real photo, sim-to-real, the team name and leader.
- **Video kit:** one edit list, [edit.yaml](../video/edit.yaml), gives the [script](../video/script.md) with the founder's exact lines and both endings, the [shot list](../video/shot_list.md) ([PDF](../video/shot_list.pdf)) and the captions; 17 shots, 2:52 planned.
  - Seven screen segments in docs/video/segments/, recorded frame by frame at 1920 x 1080 from the live Explorer and local pages (`uv run --with playwright==1.64.0 python scripts/record_screens/record.py`): the scene_0103 trap orbit, the verified panel, the headline chart, a counterfactual twin, the real quickstart and verify runs, the report scroll.
  - `scripts/make_video.sh` builds the MP4 with burnt-in captions: proven with placeholder slates (2:52) and with throwaway desk clips and one long voice memo. A real build leaves out any shot whose words are not filled yet.
  - `scripts/ask_live.py` makes the one real call the desk beat films.
- **Media pack** ([docs/media/pack/](../media/pack/)): hero triptych, camera-dial strip, gate funnel, the marked trap image, the twin before and after pair, gallery screenshots, two Explorer turntables and a GIF.
- **Form text:** [docs/submission.md](../submission.md) (challenge, short and long brief, technologies, every link). **Judge Q&A:** [docs/QA.md](../QA.md), 17 questions.
- **Review:** a reviewer pass found ten issues, all fixed (DECISIONS.md); the quickstart now gives 110 tasks, not 112 (programs 0.3.0), and the pack says so.
- **Refresh:** `uv run scripts/refresh_numbers.py` rebuilds the deck and video docs and fills the form text, the Q&A and the README headline; `--check` lists what is left. Proven on today's numbers, and on throwaway Phase 2 files.
- **Organizers' rules** in [DEMO.md](DEMO.md): no public video or slide rules; our assumptions are marked unverified.

## Placeholders left for Phase 2

- {{AUDIT}}: the founder's blind audit against the models (docs/reports/v1/human_vs_models.json); also the video's audit card and line.
- {{SIM2REAL}}: real photos against their renders, and which of the two endings (out/twins_real/sim_to_real/summary.json).
- {{REAL_TRAP}}: the H1 photo's result and the photo itself on slide 9.
- Founder-only: team name and team leader, the video link and the slides link (docs/submission_inputs.json).

## Running cost of model calls

- VTF-09: none (the live-ask tool was tried with the offline fake model). Total still about $46.16.

## Asks

- **Blind audit:** finish the 150 in out/v1/audit/index.html (main checkout), export the JSON, and say where it is.
- **Photo session:** print docs/twins/mat_A3.pdf at 100%, buy the 10 pieces (docs/twins/shopping_list.md), shoot H1 to H3 first (docs/twins/photo_session.pdf), AirDrop the photos into twins/inbox/ in the main checkout and say so.
- **Team details:** say your team name and team leader name exactly as registered on hack2skill (title slide and form).
- **Video and slide rules:** look on the dashboard's submission page, or ask at the 2 Dec AMA: the video's length limit and format, and whether the video and slides go in as links. We assume 3 minutes and links.
- **After Phase 2:** film the four desk shots and read the voice-over, per docs/video/shot_list.pdf; AirDrop everything into video/inbox/ in the main checkout and say so. Names don't matter.
- **At the end:** upload the video (unlisted YouTube) and the slides (Google Slides, anyone with the link) and say both links.
- **Keys:** revoke the old Anthropic key in the Anthropic Console, if not done yet.
- Turn on two-factor authentication for Vercel: https://vercel.com/account/settings/authentication?setup=authenticator

## Next (VTF-08 Phase 2, when the founder says the audit and photos are ready)

0. **First:** `git pull` in the main checkout. VTF-08 Phase 1 and VTF-09 were fast-forwarded onto main on GitHub without touching the main checkout during the audit.
1. **Audit:** `uv run python scripts/audit_vs_models.py --run out/v1 --answers <export>`, using the scale-stated answers by default. Label every disagreement (our bug, ambiguous, human error). Human vs each model, with intervals.
2. **Photos:** `vtf twin register` (hero photos named H1_front_1.jpg or recognised), reshoot list in NOW.md.
3. **Sim-to-real:** all four settings on the real tasks and their siblings; say which ending we have.
4. **Video, Phase 2 shots:**
   - S1: record the real call on the H1 photo while `uv run python scripts/ask_live.py <H1 photo's real task> --model claude-opus-5-5` runs (docs/video/shot_list.md). It saves the answer in out/video/live_call.json, and the S1 voice-over line takes it from there, so the words match the screen. If the photo does not fool Opus, take the next layout in trap_check.md section 4 and rewrite S1, D2 and S2 in docs/video/edit.yaml.
   - S2: export H1's digital twin to the Explorer and record it rotating beside the photo.
5. **Redeploy:** findings, risk cards, report, Hugging Face, Explorer, README. By hand, as the refresh does not touch them: the README's "Limits" line (the audit and photos "are still being done"), VALIDATION.md's G8 row ("real photos to come") and its "Founder blind audit" placeholder section.
6. **Last step: `uv run scripts/refresh_numbers.py`, then `uv run scripts/refresh_numbers.py --check`.** It fills every placeholder in the slides, the video captions and script, docs/submission.md, docs/QA.md and the README headline. Then the founder records (Asks), `scripts/make_video.sh` builds the video, and the founder uploads and submits (P28, P29).
