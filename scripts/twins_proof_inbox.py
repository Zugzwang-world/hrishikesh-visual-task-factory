"""Stand-in photo inbox from the rendered digital twins, to prove the photo pipeline end to end
before real photos exist.

    uv run python scripts/twins_proof_inbox.py out/twins_digital out/twins_proof/inbox
    scripts/blender_lock.sh twins_proof -- uv run vtf twin register out/twins_proof/inbox \
        --out out/twins_proof
    uv run vtf twin sim-to-real out/twins_proof --model fake-oracle

Every view of every layout (and the misplaced control's views) becomes a "photo" under an
anonymous name in a seeded order, so `vtf twin register` must recognise the layout and the spot
itself; the first layout's views keep L01_<spot> names to exercise naming. inbox_truth.json (next
to the inbox) records which file is which.
"""

import json
import random
import shutil
import sys
from pathlib import Path

from vtf.core.layout import RunLayout
from vtf.twin.digital import MISPLACED_RUN, layout_label


def main(twins: Path, inbox: Path, selection: Path = Path("docs/twins/selection.json")) -> None:
    sel = json.loads(selection.read_text())
    run, neg = RunLayout(twins), RunLayout(twins / MISPLACED_RUN)
    control = json.loads((neg.root / "misplacement.json").read_text())
    views = []
    for i, sid in enumerate(sel["scene_ids"]):
        for cam in ("front", "overhead", "side"):
            views.append((layout_label(i), sid, cam, "correct", run.render_dir(sid)))
    labels = {sid: layout_label(i) for i, sid in enumerate(sel["scene_ids"])}
    for cam in ("front", "overhead", "side"):
        sid = control["scene_id"]
        views.append((labels[sid], sid, cam, "misplaced", neg.render_dir(sid)))
    random.Random(7).shuffle(views)
    if inbox.exists():
        shutil.rmtree(inbox)
    inbox.mkdir(parents=True)
    truth = {}
    for n, (label, sid, cam, kind, rdir) in enumerate(views, 1):
        name = f"{label}_{cam}.png" if label == "L01" and kind == "correct" else f"IMG_{n:04d}.png"
        shutil.copyfile(rdir / cam / "beauty.png", inbox / name)
        truth[name] = {"label": label, "scene_id": sid, "spot": cam, "kind": kind}
    (inbox.parent / "inbox_truth.json").write_text(json.dumps(truth, indent=2) + "\n")
    print(f"{len(truth)} stand-in photos -> {inbox}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
