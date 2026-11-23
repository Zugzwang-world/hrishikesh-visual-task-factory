"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { projectPoint } from "@/lib/geometry";
import { answerText, ROLE_WORDS, taskHighlights, type Highlights, type Role } from "@/lib/highlights";
import type {
  CameraData,
  ExplorerIndex,
  GalleryItem,
  ModelAnswer,
  ModelSummary,
  SceneData,
  SceneSummary,
  TaskData,
} from "@/lib/types";
import type { LabelMode, ViewMode } from "./SceneView3D";

const SceneView3D = dynamic(() => import("./SceneView3D"), {
  ssr: false,
  loading: () => <div className="canvas-wrap canvas-loading">Loading 3D view…</div>,
});

const DATA = "/data";
const RIGS = ["overhead", "front", "gripper"];
const SHOWN_GATES = ["G1", "G2", "G3", "G4", "G5", "G6", "G7"];

const FAMILY_LABEL: Record<string, string> = {
  count: "Count",
  count_attribute: "Count by attribute",
  nearest_to_gripper: "Nearest to gripper",
  distance: "Distance (cm)",
  front_to_back: "Front to back",
  relation: "Relation",
  support: "Support",
  containment: "Containment",
  occlusion: "Occlusion",
  reachability: "Reachability",
  free_space: "Free space",
  gripper_view: "Gripper view",
};

const GATE_NAME: Record<string, string> = {
  G1: "Truth by execution",
  G2: "Second computation",
  G3: "Evidence visible",
  G4: "No near-ties",
  G5: "Vision necessity",
  G6: "Counterfactual twin",
  G7: "Determinism",
};

type Layout = "split" | "photo" | "3d";
type View = "scenes" | "gallery";

function familyLabel(f: string) {
  return FAMILY_LABEL[f] ?? f.replace(/_/g, " ");
}

function fmt(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (Array.isArray(v)) return v.map(fmt).join(", ");
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function pct(x: number | null | undefined) {
  return x === null || x === undefined ? "—" : `${(x * 100).toFixed(1)}%`;
}

function displayName(m: ModelSummary) {
  return m.display_name || m.label;
}

function Mark({ ok }: { ok: boolean | null }) {
  if (ok === null) return <span className="mark mark-none" aria-label="not scored" />;
  return ok ? (
    <span className="mark mark-ok" aria-label="correct">
      <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden>
        <path d="M3 8.5l3.2 3L13 4.5" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </span>
  ) : (
    <span className="mark mark-bad" aria-label="wrong">
      <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden>
        <path d="M4 4l8 8M12 4l-8 8" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
      </svg>
    </span>
  );
}

function readUrl(): { view: View; scene: string | null; task: number | null } {
  if (typeof window === "undefined") return { view: "scenes", scene: null, task: null };
  const p = new URLSearchParams(window.location.search);
  const t = p.get("task");
  return {
    view: p.get("view") === "gallery" ? "gallery" : "scenes",
    scene: p.get("scene"),
    task: t !== null && /^\d+$/.test(t) ? Number(t) - 1 : null,
  };
}

function writeUrl(changes: Record<string, string | null>) {
  const p = new URLSearchParams(window.location.search);
  for (const [k, v] of Object.entries(changes)) {
    if (v === null) p.delete(k);
    else p.set(k, v);
  }
  const q = p.toString();
  window.history.replaceState(null, "", `${window.location.pathname}${q ? `?${q}` : ""}`);
}

// ---------------------------------------------------------------- header and scene strip

function Header({ index, view, setView }: { index: ExplorerIndex; view: View; setView: (v: View) => void }) {
  const s = index.source;
  const shownTasks = index.scenes.reduce((a, b) => a + b.n_tasks, 0);
  const nGallery = index.gallery?.length ?? 0;
  return (
    <header className="header">
      <div className="brand">
        <h1>Task Explorer</h1>
        <p className="sub">
          Visual Task Factory · run <b>{s.run}</b> · {index.scenes.length} of {s.n_scenes} scenes ·{" "}
          {shownTasks} of {s.n_tasks} tasks
        </p>
      </div>
      <div className="model-chips" aria-label="Model accuracy over the whole run">
        {index.models.map((m) => (
          <div
            className={`model-chip${m.with_image === false ? " model-chip-blind" : ""}`}
            key={m.label}
            title={`${m.label}: ${m.correct} of ${m.n} tasks correct across the whole run${m.with_image === false ? " (question only, no image)" : ""}`}
          >
            <span className="model-name">{displayName(m)}</span>
            <span className="model-acc">{pct(m.accuracy)}</span>
            <span className="model-n">on {m.n}</span>
          </div>
        ))}
      </div>
      {nGallery > 0 && (
        <nav className="view-tabs seg" aria-label="View">
          <button className={view === "scenes" ? "on" : ""} aria-pressed={view === "scenes"} onClick={() => setView("scenes")}>
            Scenes
          </button>
          <button className={view === "gallery" ? "on" : ""} aria-pressed={view === "gallery"} onClick={() => setView("gallery")}>
            Conflict gallery <span className="count">{nGallery}</span>
          </button>
        </nav>
      )}
    </header>
  );
}

function SceneStrip({ scenes, active, onPick }: { scenes: SceneSummary[]; active: string; onPick: (id: string) => void }) {
  const navRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const nav = navRef.current;
    const card = nav?.querySelector<HTMLElement>(".scene-card.active");
    if (!nav || !card) return;
    // Scroll the strip only (never the page) so the active scene is in view.
    const left = card.offsetLeft - nav.offsetLeft;
    if (left < nav.scrollLeft || left + card.offsetWidth > nav.scrollLeft + nav.clientWidth)
      nav.scrollTo({ left: Math.max(0, left - 12), behavior: "smooth" });
  }, [active]);
  return (
    <nav className="scenes" aria-label="Scenes" ref={navRef}>
      {scenes.map((s) => {
        const score = Object.values(s.models)[0];
        return (
          <button
            key={s.scene_id}
            className={`scene-card${s.scene_id === active ? " active" : ""}`}
            onClick={() => onPick(s.scene_id)}
            aria-pressed={s.scene_id === active}
          >
            {s.thumb ? <img src={`${DATA}/${s.thumb}`} alt="" loading="lazy" /> : <div className="thumb-empty" />}
            <span className="scene-id">
              {s.scene_id}
              {s.gallery && <span className="tag tag-gallery">gallery</span>}
            </span>
            <span className="scene-meta">
              {s.n_tasks} tasks · {Object.keys(s.families).length} families
              {score ? ` · ${score.correct}/${score.n}` : ""}
            </span>
          </button>
        );
      })}
    </nav>
  );
}

// ---------------------------------------------------------------- legend

function Legend({ roles, boolTruth = false }: { roles: Role[]; boolTruth?: boolean }) {
  const words: Record<Role, string> = {
    truth: boolTruth ? "objects the true answer rests on" : ROLE_WORDS.truth,
    shortcut: `${ROLE_WORDS.shortcut} answer`,
    pick: "a model's wrong pick",
    evidence: "evidence for the question",
  };
  return (
    <span className="legend">
      {roles.map((r) => (
        <span key={r} className="legend-item">
          <span className={`swatch swatch-${r}`} aria-hidden /> {words[r]}
        </span>
      ))}
    </span>
  );
}

// ---------------------------------------------------------------- camera image

function CameraImage({ scene, cam, hl, labels }: { scene: SceneData; cam: CameraData; hl: Highlights; labels: LabelMode }) {
  const res = cam.resolution ?? cam.image_size ?? [1024, 768];
  const quiet = [...hl.role.keys()].filter((id) => !hl.always.has(id)).length;
  const marked = scene.objects.filter((o) => (labels === "all" || hl.role.has(o.id)) && o.position);
  return (
    <div className="image-wrap">
      {cam.image ? (
        <img src={`${DATA}/${scene.scene_id}/${cam.image}`} alt={`${cam.camera_id} camera view of ${scene.scene_id}`} />
      ) : (
        <div className="no-image">No image for this camera</div>
      )}
      {marked.map((o) => {
        const p = projectPoint(cam, o.position!);
        if (!p || p[0] < 0 || p[1] < 0 || p[0] > res[0] || p[1] > res[1]) return null;
        const role = hl.role.get(o.id);
        const showLabel =
          labels === "all" || (labels === "evidence" && (hl.always.has(o.id) || (role !== undefined && quiet <= 4)));
        return (
          <div
            key={o.id}
            className={`pin pin-${role ?? "plain"}${p[0] / res[0] > 0.62 ? " pin-left" : ""}`}
            style={{ left: `${(p[0] / res[0]) * 100}%`, top: `${(p[1] / res[1]) * 100}%` }}
            title={hl.label.get(o.id) ?? o.name}
          >
            <span className="pin-dot" />
            {showLabel && <span className="pin-label">{hl.label.get(o.id) ?? o.name}</span>}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------- task card

function ShortcutChip({ value }: { value: boolean | null }) {
  if (value === true)
    return (
      <span className="chip chip-shortcut" title="A 2D heuristic on the image gives a different answer from the 3D truth">
        2D shortcut misleads
      </span>
    );
  if (value === false)
    return (
      <span className="chip" title="The 2D heuristic agrees with the 3D truth">
        No shortcut conflict
      </span>
    );
  return (
    <span className="chip chip-muted" title="Not a spatial family, or this run does not record the 2D shortcut check">
      Shortcut check n/a
    </span>
  );
}

function ModelRow({ summary, m }: { summary: ModelSummary; m: ModelAnswer | undefined }) {
  return (
    <li className={`model-row${m ? "" : " model-row-missing"}`}>
      <span className="model-row-name">
        {displayName(summary)}
        {summary.with_image === false && <span className="tag tag-muted">no image</span>}
      </span>
      <span className="model-row-answer">
        {m ? fmt(m.answer) : <span className="muted">not in this model&apos;s sample</span>}
        {m && !m.parse_ok && <span className="tag">unparsed</span>}
        {m?.took_shortcut && <span className="tag tag-shortcut">took the 2D shortcut</span>}
      </span>
      {m ? <Mark ok={m.correct} /> : <span className="mark mark-none" aria-label="not asked" />}
    </li>
  );
}

function Gates({ task }: { task: TaskData }) {
  return (
    <ul className="gates">
      {SHOWN_GATES.map((gid) => {
        const g = task.gates[gid];
        const status = g ? (g.pass ? "pass" : "fail") : "none";
        return (
          <li key={gid} className={`gate gate-${status}`}>
            <div className="gate-head">
              <span className="gate-id">{gid}</span>
              <span className="gate-name">{GATE_NAME[gid]}</span>
              <span className="gate-status">{status === "none" ? "not run" : status}</span>
            </div>
            {g && g.key.length > 0 && (
              <div className="gate-key">
                {g.key.map(([k, v]) => (
                  <span key={k} className="kv">
                    <span className="k">{k}</span> <span className="v">{v}</span>
                  </span>
                ))}
              </div>
            )}
            {g && (
              <details className="gate-details">
                <summary>Full evidence</summary>
                <pre>{JSON.stringify(g.evidence, null, 1)}</pre>
              </details>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** Dot colour for a task: every image model right, none right, or a mix. */
function dotClass(t: TaskData) {
  const seen = t.models.filter((m) => m.with_image !== false);
  if (!seen.length) return "dot-none";
  const ok = seen.filter((m) => m.correct).length;
  return ok === seen.length ? "dot-ok" : ok === 0 ? "dot-bad" : "dot-mixed";
}

function TaskCard({ index, scene, idx, setIdx }: {
  index: ExplorerIndex; scene: SceneData; idx: number; setIdx: (i: number) => void;
}) {
  const task = scene.tasks[idx];
  const names = new Map(scene.objects.map((o) => [o.id, o.name]));
  if (!task) return <section className="card">No tasks in this scene.</section>;
  const byLabel = new Map(task.models.map((m) => [m.label, m]));
  const extra = task.models.filter((m) => !index.models.some((s) => s.label === m.label));
  return (
    <section className="card task-card" aria-live="polite">
      <div className="stepper">
        <button className="step-btn" onClick={() => setIdx(Math.max(0, idx - 1))} disabled={idx === 0} aria-label="Previous task">
          ‹
        </button>
        <div className="step-dots">
          {scene.tasks.map((t, i) => (
            <button
              key={t.task_id}
              className={`step-dot ${dotClass(t)}${t.shortcut_conflict ? " dot-conflict" : ""}${i === idx ? " current" : ""}`}
              onClick={() => setIdx(i)}
              aria-label={`Task ${i + 1}: ${familyLabel(t.family)}${t.shortcut_conflict ? ", 2D shortcut conflict" : ""}`}
              title={`${i + 1}. ${familyLabel(t.family)}${t.shortcut_conflict ? " · 2D shortcut conflict" : ""}`}
            />
          ))}
        </div>
        <span className="step-count">
          {idx + 1} / {scene.tasks.length}
        </span>
        <button
          className="step-btn"
          onClick={() => setIdx(Math.min(scene.tasks.length - 1, idx + 1))}
          disabled={idx >= scene.tasks.length - 1}
          aria-label="Next task"
        >
          ›
        </button>
      </div>

      <div className="chips">
        <span className="chip chip-family">{familyLabel(task.family)}</span>
        <span className="chip">{task.rig ?? task.camera_id ?? "camera ?"} camera</span>
        <ShortcutChip value={task.shortcut_conflict} />
      </div>

      <h2 className="question">{task.question}</h2>
      {task.question_alt && <p className="question-alt">Also asked as: {task.question_alt}</p>}

      <div className="answer-box answer-truth">
        <span className="answer-label">True answer</span>
        <span className="answer-value">
          {task.answer_text}
          {task.tolerance !== null && task.tolerance !== undefined && <span className="tol"> ± {task.tolerance}</span>}
        </span>
      </div>
      {task.shortcut_conflict && (
        <div className="answer-box answer-shortcut">
          <span className="answer-label">2D shortcut says</span>
          <span className="answer-value">{answerText(task.shortcut_answer)}</span>
        </div>
      )}

      <h3 className="section-title">Model answers</h3>
      {index.models.length || task.models.length ? (
        <ul className="models">
          {index.models.map((s) => (
            <ModelRow key={s.label} summary={s} m={byLabel.get(s.label)} />
          ))}
          {extra.map((m) => (
            <ModelRow key={m.label} summary={{ label: m.label, model: m.model, display_name: m.display_name, with_image: m.with_image, n: 0, correct: 0, accuracy: null }} m={m} />
          ))}
        </ul>
      ) : (
        <p className="muted">No model answers for this task yet.</p>
      )}

      <h3 className="section-title">Evidence objects</h3>
      <p className="evidence-list">
        {task.evidence_object_ids.map((id) => (
          <span key={id} className="ev-chip">
            {names.get(id) ?? id}
          </span>
        ))}
      </p>

      <h3 className="section-title">Gates</h3>
      <Gates task={task} />

      <footer className="task-foot">
        <span>{task.task_id}</span>
        {task.template_id && <span>{task.template_id}</span>}
        {Object.entries(task.difficulty).map(([k, v]) => (
          <span key={k}>
            {k.replace(/_/g, " ")} {fmt(v)}
          </span>
        ))}
        {task.twin_id && <span>twin {task.twin_id}</span>}
        {task.twin_of && <span>twin of {task.twin_of}</span>}
      </footer>
    </section>
  );
}

// ---------------------------------------------------------------- conflict gallery

function GalleryCard({ g, names, onOpen }: { g: GalleryItem; names: Map<string, string>; onOpen: () => void }) {
  const bool = g.answer_type === "bool";
  const took = g.models_taking_shortcut.map((k) => names.get(k) ?? k);
  return (
    <li className="g-card">
      <button className="g-open" onClick={onOpen} aria-label={`Open ${g.scene_id}, ${g.camera} camera: ${g.question}`}>
        <div className="g-thumb">
          {g.thumb ? <img src={`${DATA}/${g.thumb}`} alt="" loading="lazy" /> : <div className="thumb-empty" />}
          {g.marks.map((m) => (
            <span
              key={`${m.role}-${m.object_id}`}
              className={`g-mark g-mark-${m.role}${m.uv[0] > 0.62 ? " g-mark-left" : ""}`}
              style={{ left: `${m.uv[0] * 100}%`, top: `${m.uv[1] * 100}%` }}
              title={`${bool ? m.name : `${ROLE_WORDS[m.role]} · ${m.name}`}`}
            >
              <span className="g-dot" />
              {!bool && <span className="g-tag">{ROLE_WORDS[m.role]}</span>}
            </span>
          ))}
          <span className="g-cam">{g.camera} camera</span>
        </div>
        <div className="g-body">
          <div className="chips">
            <span className="chip chip-family">{familyLabel(g.family)}</span>
            <span className="chip chip-muted">{g.scene_id}</span>
          </div>
          <p className="g-question">{g.question}</p>
          <dl className="g-answers">
            <div className="g-row g-row-truth">
              <dt>True answer</dt>
              <dd>{answerText(g.truth)}</dd>
            </div>
            <div className="g-row g-row-shortcut">
              <dt>2D shortcut</dt>
              <dd>{answerText(g.shortcut_answer)}</dd>
            </div>
          </dl>
          {bool && <p className="g-note">Blue rings: the objects the true answer rests on.</p>}
          {took.length > 0 && (
            <p className="g-took">
              <span className="g-took-label">Took the shortcut:</span> {took.join(", ")}
            </p>
          )}
          {g.models.length > 0 && (
            <ul className="g-models" aria-label="Every model's answer">
              {g.models.map((m) => (
                <li key={m.label} className="g-model" title={`${m.display_name ?? m.label}: ${answerText(m.answer)}`}>
                  <Mark ok={m.correct} />
                  <span className="g-model-name">{m.display_name ?? m.label}</span>
                  <span className="g-model-answer">{answerText(m.answer)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </button>
    </li>
  );
}

function Gallery({ index, onOpen }: { index: ExplorerIndex; onOpen: (g: GalleryItem) => void }) {
  const names = new Map(index.models.map((m) => [m.label, displayName(m)]));
  const items = index.gallery ?? [];
  return (
    <section className="gallery" aria-labelledby="gallery-title">
      <div className="gallery-intro">
        <h2 id="gallery-title">Conflict gallery</h2>
        <p>
          Questions where a 2D shortcut (what looks nearest, lowest or closest in the picture) gives a different
          answer from the 3D truth, and models took the shortcut. Open one to see it from every camera and in 3D.
        </p>
        <Legend roles={["truth", "shortcut"]} />
      </div>
      <ul className="gallery-grid">
        {items.map((g) => (
          <GalleryCard key={g.task_id} g={g} names={names} onOpen={() => onOpen(g)} />
        ))}
      </ul>
    </section>
  );
}

// ---------------------------------------------------------------- page

export default function Explorer() {
  const [index, setIndex] = useState<ExplorerIndex | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setViewState] = useState<View>("scenes");
  const [sceneId, setSceneId] = useState<string | null>(null);
  const [scenes, setScenes] = useState<Record<string, SceneData>>({});
  const [taskIdx, setTaskIdx] = useState(0);
  const [cameraId, setCameraId] = useState<string | null>(null);
  const [mode, setMode] = useState<ViewMode>("camera");
  const [snapKey, setSnapKey] = useState(0);
  const [labels, setLabels] = useState<LabelMode>("evidence");
  const [layout, setLayout] = useState<Layout>("split");
  const [useGltf, setUseGltf] = useState(true);
  const [gltfFailed, setGltfFailed] = useState(false);
  const [pendingTask, setPendingTask] = useState<number | null>(null);

  useEffect(() => {
    fetch(`${DATA}/index.json`)
      .then((r) => {
        if (!r.ok) throw new Error(`index.json: HTTP ${r.status}`);
        return r.json();
      })
      .then((idx: ExplorerIndex) => {
        setIndex(idx);
        const u = readUrl();
        if (u.view === "gallery" && idx.gallery?.length) setViewState("gallery");
        const first = idx.scenes.find((s) => s.scene_id === u.scene) ?? idx.scenes[0];
        if (first) {
          setSceneId(first.scene_id);
          setPendingTask(u.scene === first.scene_id ? u.task : 0);
        }
      })
      .catch((e) => setError(String(e)));
  }, []);

  const scene = sceneId ? scenes[sceneId] : undefined;

  useEffect(() => {
    if (!sceneId || scenes[sceneId] || !index) return;
    const summary = index.scenes.find((s) => s.scene_id === sceneId);
    if (!summary) return;
    fetch(`${DATA}/${summary.path}`)
      .then((r) => {
        if (!r.ok) throw new Error(`${summary.path}: HTTP ${r.status}`);
        return r.json();
      })
      .then((data: SceneData) => setScenes((prev) => ({ ...prev, [sceneId]: data })))
      .catch((e) => setError(String(e)));
  }, [sceneId, scenes, index]);

  const selectTask = useCallback((sc: SceneData, i: number) => {
    const t = sc.tasks[i];
    setTaskIdx(i);
    const cam = (t && sc.cameras.find((c) => c.camera_id === t.camera_id)) ?? sc.cameras[0] ?? null;
    setCameraId(cam ? cam.camera_id : null);
    setMode("camera");
    setSnapKey((k) => k + 1);
    writeUrl({ scene: sc.scene_id, task: String(i + 1) });
  }, []);

  // When a scene arrives (or a task in it is requested), open that task.
  useEffect(() => {
    if (!scene || pendingTask === null) return;
    const i = Math.min(Math.max(pendingTask, 0), Math.max(scene.tasks.length - 1, 0));
    setPendingTask(null);
    selectTask(scene, i);
  }, [scene, pendingTask, selectTask]);

  const setView = (v: View) => {
    setViewState(v);
    writeUrl({ view: v === "gallery" ? "gallery" : null });
    window.scrollTo({ top: 0 });
  };

  const pickScene = (id: string) => {
    if (id === sceneId) return;
    setGltfFailed(false);
    setSceneId(id);
    setPendingTask(0);
  };

  const openGallery = (g: GalleryItem) => {
    if (g.scene_id !== sceneId) setGltfFailed(false);
    setSceneId(g.scene_id);
    setPendingTask(g.task_index);
    setView("scenes");
  };

  const setIdx = useCallback((i: number) => scene && selectTask(scene, i), [scene, selectTask]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (view !== "scenes" || !scene || e.target instanceof HTMLInputElement || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "ArrowRight") setIdx(Math.min(scene.tasks.length - 1, taskIdx + 1));
      if (e.key === "ArrowLeft") setIdx(Math.max(0, taskIdx - 1));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [view, scene, taskIdx, setIdx]);

  const task = scene?.tasks[taskIdx];
  const cam = scene?.cameras.find((c) => c.camera_id === cameraId) ?? scene?.cameras[0] ?? null;
  const hl = useMemo(() => taskHighlights(scene, task), [scene, task]);
  const roles = useMemo(() => {
    const present = new Set(hl.role.values());
    return (["truth", "shortcut", "pick", "evidence"] as Role[]).filter((r) => present.has(r));
  }, [hl]);
  const aspect = cam?.resolution ? `${cam.resolution[0]} / ${cam.resolution[1]}` : "4 / 3";

  if (error)
    return (
      <main className="app">
        <div className="empty">
          <h1>Task Explorer</h1>
          <p>Could not load the sample data ({error}).</p>
          <p className="muted">
            Build it with <code>uv run vtf export out/&lt;run&gt; --format explorer --out web/public/data</code>.
          </p>
        </div>
      </main>
    );
  if (!index)
    return (
      <main className="app">
        <div className="empty">Loading…</div>
      </main>
    );

  const camTabs = [
    ...RIGS.map((rig) => ({ rig, cam: scene?.cameras.find((c) => c.rig === rig) ?? null })),
    ...(scene?.cameras ?? [])
      .filter((c) => !c.rig || !RIGS.includes(c.rig))
      .map((c) => ({ rig: c.camera_id, cam: c })),
  ];

  return (
    <main className="app">
      <Header index={index} view={view} setView={setView} />

      {view === "gallery" ? (
        <Gallery index={index} onOpen={openGallery} />
      ) : (
        <>
          <SceneStrip scenes={index.scenes} active={sceneId ?? ""} onPick={pickScene} />

          {!scene ? (
            <div className="empty">Loading scene…</div>
          ) : (
            <div className="main">
              <section className="views-col">
                <div className="toolbar">
                  <div className="seg" role="tablist" aria-label="Camera">
                    {camTabs.map(({ rig, cam: c }) => (
                      <button
                        key={rig}
                        role="tab"
                        aria-selected={!!c && c.camera_id === cam?.camera_id}
                        className={c && c.camera_id === cam?.camera_id ? "on" : ""}
                        disabled={!c}
                        title={c ? `${c.camera_id} camera` : `No ${rig} camera in this scene`}
                        onClick={() => {
                          if (!c) return;
                          setCameraId(c.camera_id);
                          setMode("camera");
                          setSnapKey((k) => k + 1);
                        }}
                      >
                        {rig}
                      </button>
                    ))}
                  </div>
                  <div className="seg" aria-label="Layout">
                    {(["split", "photo", "3d"] as Layout[]).map((l) => (
                      <button key={l} className={layout === l ? "on" : ""} onClick={() => setLayout(l)}>
                        {l === "split" ? "Both" : l === "photo" ? "Image" : "3D"}
                      </button>
                    ))}
                  </div>
                </div>

                <div className={`views views-${layout}`}>
                  {layout !== "3d" && cam && (
                    <figure className="pane" style={{ aspectRatio: aspect }}>
                      <CameraImage scene={scene} cam={cam} hl={hl} labels={labels} />
                      <figcaption className="pane-tag">{cam.camera_id} image</figcaption>
                    </figure>
                  )}
                  {layout !== "photo" && (
                    <figure className="pane pane-3d" style={{ aspectRatio: aspect }}>
                      <SceneView3D
                        scene={scene}
                        dataBase={DATA}
                        camera={cam}
                        hl={hl}
                        mode={mode}
                        snapKey={snapKey}
                        labels={labels}
                        useGltf={useGltf && !gltfFailed}
                        onOrbit={() => setMode("orbit")}
                        onGltfError={() => setGltfFailed(true)}
                      />
                      <figcaption className="pane-tag">
                        {mode === "camera" ? `3D · ${cam?.camera_id ?? "default"} view · drag to orbit` : "3D · orbit"}
                      </figcaption>
                      {mode === "orbit" && cam && (
                        <button
                          className="pane-btn"
                          onClick={() => {
                            setMode("camera");
                            setSnapKey((k) => k + 1);
                          }}
                        >
                          Back to {cam.camera_id} view
                        </button>
                      )}
                    </figure>
                  )}
                </div>

                <div className="view-opts">
                  {roles.length > 0 && <Legend roles={roles} boolTruth={hl.boolTruth} />}
                  <label>
                    Labels{" "}
                    <select value={labels} onChange={(e) => setLabels(e.target.value as LabelMode)}>
                      <option value="evidence">marked objects</option>
                      <option value="all">all objects</option>
                      <option value="none">none</option>
                    </select>
                  </label>
                  {scene.gltf ? (
                    <label>
                      <input type="checkbox" checked={useGltf && !gltfFailed} disabled={gltfFailed} onChange={(e) => setUseGltf(e.target.checked)} />{" "}
                      glTF meshes{gltfFailed ? " (failed to load; showing primitives)" : ""}
                    </label>
                  ) : (
                    <span className="muted">3D from the scene file (primitives); no glTF for this scene</span>
                  )}
                </div>
              </section>

              <TaskCard index={index} scene={scene} idx={taskIdx} setIdx={setIdx} />
            </div>
          )}
        </>
      )}

      <footer className="page-foot">
        <p>
          Every true answer is computed by a program run on the scene file, never by a model or by hand. Gates G1 to G7
          are model-free checks; a task ships only if its gates pass. The 2D shortcut is the answer a picture-only rule
          gives (nearest in the image, lowest in the frame); a shortcut conflict is a task where it is wrong.
        </p>
        <p className="muted">
          Run {index.source.run} · recipe {index.source.recipe ?? "—"} · seed {index.source.run_seed ?? "—"} · scene spec{" "}
          {index.source.spec_version ?? "—"} · {index.source.generator_version ?? ""}
        </p>
      </footer>
    </main>
  );
}
