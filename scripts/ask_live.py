"""Ask one model one task, live, and print it plainly: the on-screen "real call" of the video.

    uv run python scripts/ask_live.py <task dir or task.json> --model claude-opus-5-5

The video's desk beat shows the real H1 photo going to Claude and Claude's answer coming back
(brief VTF-09, demo story beat 2). This makes one real request through the same adapter, prompt
and scorer as `vtf eval` (no cache: it is a fresh call), and prints the photo, the question, the
model's raw reply, its parsed answer, the true answer and the verdict, with the request ID.
Nothing is typed by hand and nothing is replayed from a log. Record the terminal while it runs
(scripts/record_screens/ replays a captured terminal; see docs/video/shot_list.md, S1).

Phase 2 runs it on the H1 photo's real task (out/twins_real/real/tasks/<id>, the pinned tape
question). `--model fake-oracle` tries it offline for free. The answer is kept in
out/video/live_call.json, and scripts/demo_numbers.py takes the S1 voice-over line from it, so
the line spoken matches the answer on screen.
"""

import argparse
import json
import sys
import time
from pathlib import Path

from vtf.core.schemas import TaskPackage
from vtf.eval.adapters import make_adapter
from vtf.eval.config import resolve_model
from vtf.eval.runner import score_task


def slow(text: str, delay: float = 0.012) -> None:
    for ch in text:
        sys.stdout.write(ch)
        sys.stdout.flush()
        time.sleep(delay)
    sys.stdout.write("\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("task", help="a task folder (task.json and its image) or the task.json")
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--save", default="out/video/live_call.json",
                    help="the answer, kept so the voice-over matches the screen")  # fmt: skip
    args = ap.parse_args()
    path = Path(args.task)
    task_json = path / "task.json" if path.is_dir() else path
    task = TaskPackage.model_validate(json.loads(task_json.read_text()))
    image = task_json.parent / task.image
    spec = resolve_model(args.model)
    adapter = make_adapter(spec)

    effort = spec.settings.get("effort")
    label = f"{spec.model_id}" + (f" (effort {effort})" if effort else "")
    slow(f"photo:    {image.name}  ({task.scene_id}, {task.camera_id} camera)")
    slow(f"question: {task.question}")
    slow(f"model:    {label}")
    sys.stdout.write("asking ...")
    sys.stdout.flush()
    t0 = time.time()
    resp = adapter.answer(image, task.question, task.answer_type)
    sys.stdout.write(f" {time.time() - t0:.1f} s\n")
    if resp.error:
        sys.exit(f"no answer: {resp.error}")
    score = score_task(task, resp.raw_text)
    answer = {True: "yes", False: "no"}.get(score.parsed, score.parsed)
    truth = {True: "yes", False: "no"}.get(task.answer, task.answer)
    slow(f"reply:    {resp.raw_text.strip()}")
    slow(f"answer:   {answer}")
    slow(f"truth:    {truth}  (from the scene file: {task.answer_text})")
    slow("verdict:  " + ("right" if score.correct else "WRONG"))
    request_id = (resp.meta or {}).get("request_id")
    if request_id:
        print(f"request:  {request_id}")
    save = Path(args.save)
    save.parent.mkdir(parents=True, exist_ok=True)
    save.write_text(json.dumps({"task_id": task.task_id, "model": spec.key, "answer": score.parsed,
                                "correct": score.correct, "raw": resp.raw_text,
                                "request_id": request_id}, indent=2) + "\n")  # fmt: skip


if __name__ == "__main__":
    main()
