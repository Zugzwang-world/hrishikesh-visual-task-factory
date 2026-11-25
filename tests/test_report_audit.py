"""Report and audit lane: G5 balance and baselines, eval sampling and splits, the blind audit page
and the dataset report, on a small synthetic contract-v0.2 run (offline, fake models only)."""

import hashlib
import json
import random
from html.parser import HTMLParser
from pathlib import Path

import pytest
from PIL import Image
from typer.testing import CliRunner

from vtf.cli import app
from vtf.core.io import read_jsonl, read_task, write_jsonl, write_model
from vtf.core.layout import RunLayout
from vtf.core.program import ProgramRef
from vtf.core.task import EvalRecord, GateResult, ManifestRow, ObjectRef, TaskPackage, Versions
from vtf.eval.blind import BLIND_INTRO, blind_key
from vtf.eval.config import resolve_model
from vtf.eval.runner import eval_run, manifest_rows, per_group_sample, stratified_sample
from vtf.eval.summary import bootstrap_ci
from vtf.validate.balance import balance_run
from vtf.validate.gates.g5_vision_necessity import (
    blind_baseline,
    capped_counts,
    effective_cap,
    prior_baseline,
    task_key,
)

NAMES = ["red box", "blue cone", "green sphere", "yellow cylinder", "purple box", "orange cone"]
# family, answer type, spatial, rigs
FAMILY_SPECS = [
    ("count", "int", False, ("front", "overhead", "gripper")),
    ("nearest_to_gripper", "object", True, ("front", "overhead", "gripper")),
    ("support", "object", False, ("front", "overhead")),
    ("containment", "set", False, ("front", "overhead")),
    ("free_space", "bool", True, ("front", "overhead", "gripper")),
    ("gripper_view", "object", False, ("gripper",)),
]
VERSIONS = Versions(
    vtf="0.0.1", spec="0.2.0", generator="synthetic", programs="synthetic", renderer="none"
)


def _answer(rng: random.Random, family: str, answer_type: str):
    if answer_type == "int":
        return rng.choices([3, 4, 5, 6, 7], weights=[6, 2, 1, 1, 1])[0]
    if answer_type == "bool":
        return rng.random() < 0.8
    if answer_type == "set":
        return [] if rng.random() < 0.6 else sorted(rng.sample(NAMES, 2))
    if family == "support" and rng.random() < 0.7:
        return "purple box"  # the scorer on main reads colour + shape names only
    return rng.choices(NAMES, weights=[5, 1, 1, 1, 1, 1])[0]


def _text(answer) -> str:
    if isinstance(answer, bool):
        return "yes" if answer else "no"
    if isinstance(answer, list):
        return ", ".join(answer) if answer else "none"
    return str(answer)


def make_synthetic_run(root: Path, n_per_family: int = 24, seed: int = 7) -> Path:
    """A validated contract-v0.2 run folder with deliberately skewed answers."""
    rng = random.Random(seed)
    lay = RunLayout(root)
    objects = [
        ObjectRef(id=f"obj_{i:02d}", name=n, category=n.split()[1], color_name=n.split()[0])
        for i, n in enumerate(NAMES)
    ]
    manifest, rejected = [], []
    for family, answer_type, spatial, rigs in FAMILY_SPECS:
        for i in range(n_per_family):
            rig = rigs[i % len(rigs)]
            scene_id = f"scene_{i % 8:04d}"
            answer = _answer(rng, family, answer_type)
            task_id = "t" + hashlib.sha256(f"{family}/{i}/{seed}".encode()).hexdigest()[:16]
            gates = {
                g: GateResult(passed=True, evidence={"synthetic": True})
                for g in ("G1", "G2", "G3", "G4", "G7")
            }
            task = TaskPackage(
                task_id=task_id,
                scene_id=scene_id,
                scene_hash="0" * 64,
                camera_id=rig,
                rig=rig,
                question=f"Synthetic {family} question {i}?",
                family=family,
                template_id=f"{family}.p1",
                program=ProgramRef(name=family),
                answer=answer,
                answer_type=answer_type,
                answer_text=_text(answer),
                shortcut_conflict=(rng.random() < 0.3) if spatial else None,
                evidence_object_ids=["obj_00"],
                scene_objects=objects,
                gates=gates,
                difficulty={
                    "n_objects": rng.randint(4, 12),
                    "clutter": rng.choice(["low", "mid", "high"]),
                    "camera_quality": rng.choice(["clean", "cctv"]),
                },
                seed=i,
                versions=VERSIONS,
            )
            tdir = lay.task_dir(task_id)
            tdir.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (32, 24), (40 + 5 * i, 90, 140)).save(tdir / "image.png")
            write_model(tdir / "task.json", task)
            manifest.append(
                ManifestRow(
                    task_id=task_id,
                    scene_id=scene_id,
                    camera_id=rig,
                    rig=rig,
                    family=family,
                    answer_type=answer_type,
                    shortcut_conflict=task.shortcut_conflict,
                    path=f"tasks/{task_id}/task.json",
                )
            )
    for i in range(15):
        rejected.append(
            {
                "scene_id": f"scene_{i % 8:04d}",
                "camera_id": "front",
                "family": "count",
                "stage": ["compile", "G3", "G2"][i % 3],
                "reason": ["near_tie_distance", "evidence_hidden", "pixel_mismatch"][i % 3],
                "detail": {},
            }
        )
    write_jsonl(lay.task_manifest, manifest)
    write_jsonl(lay.rejected, rejected)
    n = len(manifest)
    gate_stats = {
        g: {"run": n + 5, "pass": n, "pass_rate": round(n / (n + 5), 4)}
        for g in ("G1", "G2", "G3", "G4", "G7")
    }
    gate_stats["G6"] = {"run": 10, "pass": 8, "pass_rate": 0.8}
    lay.validate_report.write_text(
        json.dumps(
            {
                "scenes": 8,
                "candidates": n + len(rejected),
                "accepted": n,
                "rejected": len(rejected),
                "gates": gate_stats,
                "families": {},
                "rejection_reasons": {},
            }
        )
    )
    lay.render_report.write_text(
        json.dumps(
            {
                "device": "GPU",
                "scenes": {
                    f"scene_{i:04d}": {
                        "status": "ok",
                        "seconds": 20 + i,
                        "timings": {
                            "build": 0.2,
                            "settle": 0.1,
                            "render": 18 + i,
                            "data_passes": 0.3,
                            "amodal": 0.8,
                            "total": 19.4 + i,
                        },
                    }
                    for i in range(8)
                },
            }
        )
    )
    return root


class WellFormed(HTMLParser):
    """Checks that every non-void element is closed in order."""

    VOID = {
        "meta",
        "link",
        "br",
        "img",
        "input",
        "hr",
        "source",
        "col",
        "area",
        "base",
        "wbr",
        "path",
        "rect",
        "line",
        "circle",
    }

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"unexpected </{tag}> with open {self.stack[-3:]}")
            return
        self.stack.pop()


def assert_well_formed(text: str) -> None:
    checker = WellFormed()
    checker.feed(text)
    checker.close()
    assert not checker.errors, checker.errors[:5]
    assert not checker.stack, f"unclosed: {checker.stack}"


@pytest.fixture(autouse=True)
def cache_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "eval_cache.sqlite"
    monkeypatch.setenv("VTF_EVAL_CACHE", str(path))
    return path


@pytest.fixture
def run(tmp_path: Path) -> Path:
    return make_synthetic_run(tmp_path / "run")


def quiet(_: str) -> None:
    pass


# --- sampling and splits ----------------------------------------------------------------------


def test_stratified_sample_is_seeded_and_even(run: Path) -> None:
    rows = manifest_rows(run)
    a = stratified_sample(rows, 28, ("family", "rig"), seed=1)
    assert [r.task_id for r in a] == [
        r.task_id for r in stratified_sample(rows, 28, ("family", "rig"), 1)
    ]
    assert [r.task_id for r in a] != [
        r.task_id for r in stratified_sample(rows, 28, ("family", "rig"), 2)
    ]
    assert len(a) == 28
    strata = {}
    for r in a:
        strata[(r.family, r.rig)] = strata.get((r.family, r.rig), 0) + 1
    assert len(strata) == 14 and set(strata.values()) == {2}
    assert [r.task_id for r in a] == [r.task_id for r in rows if r in a], "manifest order kept"
    per = per_group_sample(rows, 5, "family", ("rig",), seed=0)
    counts = {}
    for r in per:
        counts[r.family] = counts.get(r.family, 0) + 1
    assert set(counts.values()) == {5}


def test_bootstrap_interval() -> None:
    low, high = bootstrap_ci(30, 60)
    assert low < 0.5 < high and (low, high) == bootstrap_ci(30, 60)
    assert bootstrap_ci(10, 10) == (1.0, 1.0)
    assert bootstrap_ci(0, 0) is None


def test_eval_sample_and_splits(run: Path) -> None:
    summary = eval_run(run, "fake-oracle", sample=26, stratify=("family", "rig"), echo=quiet)
    assert summary.n == 26 and summary.accuracy == 1.0 and summary.ci95 == (1.0, 1.0)
    assert set(summary.splits) == {
        "family",
        "rig",
        "shortcut_conflict",
        "family_x_shortcut_conflict",
    }
    assert set(summary.splits["rig"]) == {"front", "overhead", "gripper"}
    assert set(summary.splits["shortcut_conflict"]) <= {"true", "false", "n/a"}
    assert "count|n/a" in summary.splits["family_x_shortcut_conflict"]
    out = RunLayout(run).eval_dir("fake-oracle")
    records = [EvalRecord.model_validate(r) for r in read_jsonl(out / "results.jsonl")]
    assert all(r.rig and r.camera_id and r.with_image for r in records)
    assert {r.shortcut_conflict for r in records if r.family == "free_space"} <= {True, False}
    selection = json.loads((out / "selection.json").read_text())
    assert selection["n"] == 26 and selection["stratify"] == ["family", "rig"]

    constant = eval_run(run, "fake-constant", sample=26, echo=quiet)
    assert constant.accuracy < 1.0
    stats = constant.splits["family"]["count"]
    assert stats.ci95_low is not None and stats.ci95_low <= stats.accuracy <= stats.ci95_high


def test_cli_eval_sample(run: Path) -> None:
    args = ["eval", str(run), "--model", "fake-oracle", "--sample", "12", "--stratify", "family"]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert "rig front" in result.output and "100.0%" in result.output


# --- G5 ----------------------------------------------------------------------------------------


def test_capping_math() -> None:
    assert capped_counts({"a": 70, "b": 30}, 0.55) == {"a": 36, "b": 30}
    kept = capped_counts({"a": 60, "b": 20, "c": 10, "d": 10}, 0.35)
    assert max(kept.values()) / sum(kept.values()) <= 0.35
    assert effective_cap(0.35, 2) == pytest.approx(0.55)
    assert effective_cap(0.35, 1) == 1.0
    assert effective_cap(0.35, 4) == 0.35
    assert effective_cap(None, 3) is None


def test_prior_baseline_numbers() -> None:
    base = dict(
        scene_id="s",
        scene_hash="0" * 64,
        camera_id="front",
        question="q",
        family="distance",
        template_id="distance.p1",
        program=ProgramRef(name="distance_cm"),
        answer_type="number",
        evidence_object_ids=[],
        gates={},
        seed=0,
        versions=VERSIONS,
    )
    tasks = [
        TaskPackage(task_id=f"t{i}", answer=v, answer_text=str(v), **base)
        for i, v in enumerate([10.0, 12.0, 30.0, 31.0, 50.0])
    ]
    prior = prior_baseline(tasks)
    fam = prior["families"]["distance"]
    assert fam["prior_answer"] == 30.0 and fam["correct"] == 2  # 30 and 31 within max(2, 10%)


def test_balance_caps_answers_and_is_idempotent(run: Path) -> None:
    lay = RunLayout(run)
    before = len(list(read_jsonl(lay.task_manifest)))
    report = balance_run(run, seed=0, echo=quiet)
    rows = list(read_jsonl(lay.task_manifest))
    rejected = list(read_jsonl(lay.rejected))
    g5_rows = [r for r in rejected if r["stage"] == "G5"]
    assert report["dropped"] == len(g5_rows) == before - len(rows) > 0
    assert all(r["reason"] == "rebalance" and r["detail"]["task_id"] for r in g5_rows)
    assert len(rejected) - len(g5_rows) == 15, "earlier rejections are kept"

    tasks = [read_task(run / r["path"]) for r in rows]
    for family, f in report["families"].items():
        members = [t for t in tasks if t.family == family]
        counts = {}
        for t in members:
            counts[task_key(t)] = counts.get(task_key(t), 0) + 1
        if f["cap"] is not None and not f["single_answer"]:
            assert max(counts.values()) / len(members) <= f["cap"] + 1e-9, family
    free = [t for t in tasks if t.family == "free_space"]
    share = sum(bool(t.answer) for t in free) / len(free)
    assert 0.45 <= share <= 0.55

    g5 = tasks[0].gates["G5"]
    assert g5.passed and g5.evidence["family_prior_accuracy"] is not None
    assert list(tasks[0].gates) == sorted(tasks[0].gates)
    dropped = read_task(lay.task_dir(g5_rows[0]["detail"]["task_id"]) / "task.json")
    assert not dropped.gates["G5"].passed and dropped.gates["G5"].evidence["reason"] == "rebalance"

    prior = json.loads((lay.baselines_dir / "prior.json").read_text())
    assert (
        prior["before_balance"]["families"]["support"]["accuracy"]
        > prior["after"]["families"]["support"]["accuracy"]
    )
    assert (lay.baselines_dir / "balance.json").is_file()

    manifest_bytes = lay.task_manifest.read_bytes()
    again = balance_run(run, seed=0, echo=quiet)
    assert lay.task_manifest.read_bytes() == manifest_bytes
    assert again["dropped"] == report["dropped"] and again["pool"] == before


def test_blind_mode_request_has_no_image(run: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from vtf.eval.blind import make_blind_adapter

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")
    adapter = make_blind_adapter(resolve_model("claude-sonnet-5-5"))
    image = next(RunLayout(run).tasks_dir.glob("*/image.png"))
    kwargs = adapter.request(image, "How many objects?", "int", True)
    content = kwargs["messages"][0]["content"]
    assert content == [{"type": "text", "text": "How many objects?"}]
    assert kwargs["system"].startswith(BLIND_INTRO) and '{"answer": ...}' in kwargs["system"]


def test_blind_baseline_with_fake_model(run: Path) -> None:
    balance_run(run, echo=quiet)
    result = blind_baseline(run, model="fake-constant", per_family=4, echo=quiet)
    lay = RunLayout(run)
    records = list(read_jsonl(lay.eval_dir(blind_key("fake-constant")) / "results.jsonl"))
    assert len(records) == 4 * len(FAMILY_SPECS) and not any(r["with_image"] for r in records)
    assert all(r["model"] == "fake-constant__blind" for r in records)
    assert set(result["families"]) == {f for f, *_ in FAMILY_SPECS}
    assert result["families"]["count"]["prior_accuracy"] is not None
    task = read_task(run / next(read_jsonl(lay.task_manifest))["path"])
    assert task.gates["G5"].evidence["blind_model"] == "fake-constant__blind"
    assert task.gates["G5"].evidence["blind_accuracy"] is not None


# --- audit ------------------------------------------------------------------------------------


def _page_data(page: Path) -> dict:
    text = page.read_text()
    start = text.index('id="audit-data">') + len('id="audit-data">')
    return json.loads(text[start : text.index("</script>", start)])


def test_audit_make_and_score(run: Path) -> None:
    from vtf.package.audit import make_audit, score_audit

    page = make_audit(run, n=30, seed=0, echo=quiet)
    text = page.read_text()
    assert_well_formed(text)
    assert "http://" not in text and "https://" not in text, "works offline"
    data = _page_data(page)
    items = data["tasks"]
    assert len(items) == 30
    assert all(
        set(item) == {"id", "img", "q", "type", "family", "rig", "camera", "options", "unit"}
        for item in items
    ), "never the answer"
    assert all((page.parent / item["img"]).is_file() for item in items)
    sample = json.loads((page.parent / "sample.json").read_text())
    assert sample["task_ids"] == [item["id"] for item in items]
    assert len(sample["strata_counts"]) >= 14
    assert make_audit(run, n=30, seed=0, echo=quiet).read_text() == text, "seeded"

    answers = {}
    for item in items:
        task = read_task(RunLayout(run).task_dir(item["id"]) / "task.json")
        answers[item["id"]] = {"answer": task.answer, "flag": False, "note": ""}
    wrong, cant = items[0]["id"], items[1]["id"]
    truth = read_task(RunLayout(run).task_dir(wrong) / "task.json")
    answers[wrong]["answer"] = {
        "int": 99,
        "bool": not truth.answer,
        "set": ["red box", "blue cone", "green sphere"],
        "object": "purple cone",
    }[truth.answer_type]
    answers[cant] = {"answer": None, "flag": True, "note": "too dark"}
    del answers[items[2]["id"]]
    export = {"audit_id": data["audit_id"], "task_ids": sample["task_ids"], "answers": answers}
    answers_file = run / "answers.json"
    answers_file.write_text(json.dumps(export))
    result = score_audit(run, answers_file, echo=quiet)
    assert result["audited"] == 29 and result["not_answered"] == 1
    assert result["correct"] == 27 and not result["meets_target"]
    assert {m["task_id"] for m in result["misses"]} == {wrong, cant}
    assert sum(f["n"] for f in result["families"].values()) == 29

    cli = CliRunner().invoke(app, ["audit", "score", str(run), str(answers_file)])
    assert cli.exit_code == 0, cli.output
    assert "misses:" in cli.output and "can't tell" in cli.output


def test_audit_refresh_drops_seen_scenes(run: Path) -> None:
    from vtf.eval.runner import manifest_rows, stratum_value
    from vtf.package.audit import make_audit, refresh_audit, score_audit

    page = make_audit(run, n=30, seed=0, echo=quiet)
    base = json.loads((page.parent / "sample.json").read_text())
    rows = {r.task_id: r for r in manifest_rows(run)}
    seen = sorted({rows[t].scene_id for t in base["task_ids"]})[:2]
    page = refresh_audit(run, seen, prefer_ids=base["task_ids"][:0], echo=quiet)
    assert json.loads((page.parent / "sample.json").read_text()) == base, "draw kept"
    v2 = json.loads((page.parent / "sample_v2.json").read_text())
    assert len(v2["task_ids"]) == 30 and len(set(v2["task_ids"])) == 30
    assert not {rows[t].scene_id for t in v2["task_ids"]} & set(seen)
    assert v2["replacements"] and v2["refreshed_from"] == base["audit_id"]
    keys = ("family", "rig", "shortcut_conflict")
    for rep in v2["replacements"]:
        if rep["level"] == "same_stratum":
            old, new = rows[rep["old"]], rows[rep["new"]]
            assert [stratum_value(old, k) for k in keys] == [stratum_value(new, k) for k in keys]
    data = _page_data(page)
    assert [i["id"] for i in data["tasks"]] == v2["task_ids"]
    assert data["audit_id"] == v2["audit_id"] != base["audit_id"]
    answers = {
        t: {"answer": read_task(RunLayout(run).task_dir(t) / "task.json").answer}
        for t in v2["task_ids"]
    }
    export = {"audit_id": v2["audit_id"], "answers": answers}
    (run / "answers_v2.json").write_text(json.dumps(export))
    result = score_audit(run, run / "answers_v2.json", echo=quiet)
    assert result["sample_size"] == 30 and result["correct"] == 30, "finds sample_v2.json by id"


def test_cli_audit_make(run: Path, tmp_path: Path) -> None:
    out = tmp_path / "audit_out"
    result = CliRunner().invoke(app, ["audit", "make", str(run), "--n", "10", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert (out / "index.html").is_file() and len(list((out / "img").glob("*.jpg"))) == 10


# --- dataset report ---------------------------------------------------------------------------


def test_dataset_report_end_to_end(run: Path) -> None:
    from vtf.package.audit import make_audit, score_audit

    runner = CliRunner()
    assert runner.invoke(app, ["balance", str(run)]).exit_code == 0
    blind_baseline(run, model="fake-constant", per_family=3, echo=quiet)
    eval_run(run, "fake-oracle", sample=40, echo=quiet)
    eval_run(run, "fake-constant", echo=quiet)
    page = make_audit(run, n=12, echo=quiet)
    items = _page_data(page)["tasks"]
    answers = {
        i["id"]: {"answer": read_task(RunLayout(run).task_dir(i["id"]) / "task.json").answer}
        for i in items
    }
    (run / "answers.json").write_text(json.dumps({"answers": answers}))
    score_audit(run, run / "answers.json", echo=quiet)

    result = runner.invoke(app, ["dataset-report", str(run)])
    assert result.exit_code == 0, result.output
    report = RunLayout(run).report_dir / "index.html"
    text = report.read_text()
    assert_well_formed(text)
    assert "http://" not in text and "https://" not in text
    for heading in (
        "Gate pass rates",
        "What the factory threw away",
        "Coverage",
        "Answer distributions",
        "Vision necessity (G5)",
        "Counterfactual twins (G6)",
        "Model accuracy",
        "Founder audit",
        "Render timings",
    ):
        assert heading in text, heading
    assert text.count("<svg") >= 10
    assert "fake-oracle" in text and "fake-constant__blind" not in text.split('id="models"')[1]
    assert "G6 passed on <b>8</b>" in text and "rebalance" in text
    data = json.loads((RunLayout(run).report_dir / "report_data.json").read_text())
    assert {m["name"] for m in data["models"]} == {"fake-oracle", "fake-constant"}


def test_dataset_report_on_bare_validated_run(run: Path) -> None:
    from vtf.validate.report import dataset_report

    page = dataset_report(run, echo=quiet)
    text = page.read_text()
    assert_well_formed(text)
    assert "No model has been evaluated" in text and "Not run yet" in text


def test_findings_section_reads_findings_json(tmp_path: Path) -> None:
    from vtf.validate.report import findings_section

    assert findings_section(tmp_path / "findings.json") is None
    st = {"n": 10, "correct": 5, "accuracy": 0.5, "ci95": [0.3, 0.7]}
    model = {
        "label": "Opus 5.5",
        "effort": "xhigh",
        "acc": {
            "overall": st,
            "by_conflict": {"conflict": st, "no_conflict": {**st, "accuracy": 0.8}, "n/a": st},
            "gap": {"points": 0.3, "ci95": [0.1, 0.5]},
        },
        "matched": {"gap_conflict_minus_no_conflict": -0.3, "ci95": [-0.5, -0.1]},
        "logit": {
            "conflict_odds_ratio": 0.3,
            "conflict_or_ci95_cluster": [0.2, 0.5],
            "stable": True,
        },
        "capture": {"capture_share": 0.6, "chance_share": 0.5, "ratio": 1.2, "ratio_ci95": None},
        "capture_object_choice": {
            "capture_share": 0.6,
            "chance_share": 0.2,
            "ratio": 3.0,
            "ratio_ci95": [2.0, 4.0],
        },
    }
    path = tmp_path / "findings.json"
    path.write_text(
        json.dumps({"models": {"k": model}, "verdicts": ["v"], "tasks_common": 10, "scenes": 3})
    )
    section = findings_section(path)
    assert section is not None and section["verdicts"] == ["v"]
    assert "Opus 5.5 · xhigh" in str(section["chart"]) and section["table"][0]["odds_ratio"] == 0.3
    assert section["breaking"] is None and section["twins"] is None


def test_findings_section_breaking_points_and_twins() -> None:
    from vtf.validate.report import breaking_section, twin_flip_section

    empty = {"n": 0, "accuracy": None, "ci95": None}
    bp = {
        "chart": "../../media/breaking_point.png",
        "n_tasks": 10,
        "axes": {
            "objects": {
                "title": "Objects on the table",
                "bins": ["2–5", "6–10"],
                "models": {
                    "claude-opus-5-5": {
                        "raw": [{"n": 5, "accuracy": 0.6, "ci95": [0.4, 0.8]}, empty],
                        "adjusted": [{"accuracy": 0.62}, {"accuracy": None}],
                    }
                },
            }
        },
    }
    sec = breaking_section(bp)
    assert sec["axes"][0]["models"] == ["Opus 5.5 · high"]
    assert sec["axes"][0]["rows"][0]["cells"] == ["60% (40–80) [62%]"]
    assert sec["axes"][0]["rows"][1]["cells"] == ["–"]
    share = {"share": 0.25, "ci95": [0.1, 0.4]}
    tf = {
        "chart": "../../media/twin_flip.png",
        "pairs_total": 4,
        "scenes": 2,
        "models": {
            "claude-haiku-5-5": {
                "pairs": 4,
                "both_right": {"share": 0.5, "ci95": None},
                "twin_only": share,
                "sticks": share,
                "other": {"share": 0.0, "ci95": None},
                "given_original_right": {"follows": {"share": 0.75}, "sticks": {"share": 0.25}},
            }
        },
    }
    rows = twin_flip_section(tf)["rows"]
    assert rows[0]["name"] == "Haiku 5.5 · high" and rows[0]["both"] == "50%"
    assert rows[0]["sticks"] == "25% (10–40)" and rows[0]["given"] == "75% / 25%"
    assert breaking_section(None) is None and twin_flip_section({"models": {}}) is None
