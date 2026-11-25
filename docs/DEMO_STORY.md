# VTF demo story (draft 25 Nov 2026, for Run 3: slides and video)

## One-line pitch

Robots now see through vision-language models. We built a factory that proves, with exact answers and no AI in the checking, where those models get spatial questions wrong, and we test it on real photos too.

## What sets us apart from 2D entries

| Most entries (2D) | Our factory (3D robot workcell) |
| --- | --- |
| Charts, documents and diagrams | A camera view of a world we fully know: three robot cameras on a tabletop |
| Depth, distance, occlusion and reach can't be measured | All exact: physics-settled scenes and typed programs on the scene file |
| Answers checked by metadata or by a model | Every answer proven twice without AI: the scene file, and the rendered pixels |
| Variation means swapping colour or text | Clutter, stacks, containers, occlusion, camera quality and three viewpoints |
| Synthetic only | Physical twins: real objects on a measured mat, real photos with exact answers |
| A benchmark at the end | A finding with intervals and confound checks, plus risk cards |

## Three wow moments

1. **The trap.** In the image the mug looks closest to the gripper; in 3D the bowl is. Every Claude model we tested picks the mug. Rotate the 3D view in the Explorer and the truth is obvious.
   - Every model is about 25 points worse when the 2D cue misleads.
   - More thinking doesn't help: Opus xhigh vs high changes the gap by 0 points.
   - On nearest-to-gripper, 22 of Opus xhigh's 27 misses named the object nearest in the image.
2. **It's real.** A phone photo of the real table beside its render, with the same question and each model's answer. This depends on the Run 2 result; see the claims below.
3. **Nothing trusts an AI.**
   - Move one object and the answer flips (the G6 twin).
   - Re-render and the result is identical (`vtf verify`).
   - One command gives a judge the same tasks.

## Demo video: the founder on camera with the physical setup (agreed 25 Nov)

The video carries the story. The founder appears on camera at a real desk with the printed mat and the real objects, and explains it through the physical system.

1. **At the desk:** the mat, the real objects, and a phone held low at the robot camera's height and angle (on a stack of books). "This is what a robot arm's camera sees."
2. **The question, asked for real:** the physical set has no gripper, so he asks a question that has no gripper in it, such as "which object is closest to the camera?" (front-to-back) or a relation or distance question. Pick the exact question from VTF-08's trap check (docs/twins/trap_check.md). The screen shows that exact photo going to Claude, and Claude gives the shortcut answer. Record the real call on screen; never fake an answer. The nearest-to-gripper trap stays in the rendered part (beat 3).
3. **The 3D twin:** cut to the Task Explorer with the same layout as a 3D digital twin. Rotate it: the true answer in blue, the shortcut object in orange. Show the 2D image beside it to make the problem obvious.
4. **From one example to a factory:** "We didn't find one trick question. We built a factory."
   - One recipe gives thousands of tasks, each proven by the gates with no AI in the checking.
   - The chart: every Claude model is about 25 points worse when the 2D cue misleads, and more thinking doesn't help.
   - Move one object and ask again: models get both versions right only 34% (Haiku) to 52% (Opus) of the time. They don't track the scene.
   - Hidden evidence (under 60% visible) costs 10 to 13 points, and a harsh camera costs Sonnet and Opus 12 to 16.
   - A human gets these right: the founder's audit.
5. **Close at the desk:** "Robots will act on these answers. Our factory finds the failure before the robot does." Then: test, training data and verifiable rewards for robot learning, which is what Innodata's physical-AI business needs.

Production notes for Run 3:
- Pick the on-camera layout from the Run 2 real-photo results, choosing one where the models actually fail on the real photo. Don't rely on luck.
- The phone pose must match the photo guide's camera pose, so the 3D twin in the Explorer lines up with the real shot.
- Check the organizers' video rules (length, format) before scripting.

## Demo arc (live Q&A, short form)

1. **The stakes:** a gripper camera view asks, "Which object is nearest the gripper?" A wrong answer means a wrong grasp.
2. **The trap:** an Explorer gallery scene. The image says mug, 3D says bowl, and all four Claude settings choose the mug.
3. **The proof:** one chart.
   - The gap is about 25 points for every model, and thinking harder doesn't close it.
   - It survives the confound checks.
   - A human gets these tasks right: the founder's blind audit.
4. **The factory:** one recipe produces thousands of tasks, each passing gates G1 to G8, and the same command gives identical output.
5. **Real world and close:** a real photo vs its render. The factory is a test, a source of training data and a verifiable reward for robot learning, which is what Innodata's physical-AI business needs.

## Claims we can and can't make

- Say "the Claude models we tested". No other vendors were evaluated.
- The real-photo result isn't in yet. Plan two endings:
  - same gap on real photos: "the failure is real"
  - different gap: "the factory measures the sim-to-real gap"
- The human baseline comes from the founder's blind audit. Without it, say only that the answers are verified, not that "humans get it right".
