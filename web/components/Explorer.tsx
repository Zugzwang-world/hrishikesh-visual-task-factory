"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { projectPoint } from "@/lib/geometry";
import type { CameraData, ExplorerIndex, ModelAnswer, SceneData, SceneSummary, TaskData } from "@/lib/types";
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

function readUrl(): { scene: string | null; task: number | null } {
  if (typeof window === "undefined") return { scene: null, task: null };
  const p = new URLSearchParams(window.location.search);
  const t = p.get("task");
  return { scene: p.get("scene"), task: t !== null && /^\d+$/.test(t) ? Number(t) - 1 : null };
}

function writeUrl(scene: string, task: number) {
  const p = new URLSearchParams(window.location.search);
  p.set("scene", scene);
  p.set("task", String(task + 1));
  window.history.replaceState(null, "", `${window.location.pathname}?${p.toString()}`);
}

// ---------------------------------------------------------------- header and scene strip

function Header({ index }: { index: ExplorerIndex }) {
  const s = index.source;
  const shownTasks = index.scenes.reduce((a, b) => a + b.n_tasks, 0);
  return (
    <header className="header">
      <div className="brand">
        <h1>Task Explorer</h1>
        <p className="sub">
          Visual Task Factory · run <b>{s.run}</b> · {index.scenes.length} of {s.n_scenes} scenes ·{" "}
          {shownTasks} of {s.n_tasks} tasks
        </p>
      </div>
      <div className="model-chips">
        {index.models.map((m) => (
          <div className="model-chip" key={m.label} title={`${m.correct} of ${m.n} tasks correct across the whole run`}>
            <span className="model-name">{m.label}</span>
            <span className="model-acc">{pct(m.accuracy)}</span>
            <span className="model-n">on {m.n} tasks</span>
          </div>
        ))}
      </div>
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
            <span className="scene-id">{s.scene_id}</span>
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

// ---------------------------------------------------------------- camera image

function CameraImage({ scene, cam, evidence, picks }: {
  scene: SceneData; cam: CameraData; evidence: string[]; picks: Set<string>;
}) {
  const res = cam.resolution ?? cam.image_size ?? [1024, 768];
  const ev = scene.objects.filter((o) => (evidence.includes(o.id) || picks.has(o.id)) && o.position);
  const showNames = evidence.length <= 4;
  return (
    <div className="image-wrap">
      {cam.image ? (
        <img src={`${DATA}/${scene.scene_id}/${cam.image}`} alt={`${cam.camera_id} camera view of ${scene.scene_id}`} />
      ) : (
        <div className="no-image">No image for this camera</div>
      )}
      {ev.map((o) => {
        const p = projectPoint(cam, o.position!);
        if (!p || p[0] < 0 || p[1] < 0 || p[0] > res[0] || p[1] > res[1]) return null;
        const pick = picks.has(o.id) && !evidence.includes(o.id);
        return (
          <div
            key={o.id}
            className={`pin${pick ? " pin-pick" : ""}${p[0] / res[0] > 0.62 ? " pin-left" : ""}`}
            style={{ left: `${(p[0] / res[0]) * 100}%`, top: `${(p[1] / res[1]) * 100}%` }}
            title={o.name}
          >
            <span className="pin-dot" />
            {(showNames || pick) && <span className="pin-label">{pick ? `model: ${o.name}` : o.name}</span>}
          </div>
        );
      })}
    </div>
  );
}

/** Objects named by a model's wrong answer (object answers only), to mark them in the views. */
function wrongPicks(scene: SceneData, task: TaskData | undefined): Set<string> {
  const out = new Set<string>();
  if (!task || task.answer_type !== "object") return out;
  for (const m of task.models) {
    if (m.correct || typeof m.answer !== "string") continue;
    const said = m.answer.trim().toLowerCase().replace(/^the\s+/, "");
    for (const o of scene.objects) if (o.name.toLowerCase() === said) out.add(o.id);
  }
  return out;
}

// ---------------------------------------------------------------- task card

function ShortcutChip({ value }: { value: boolean | null }) {
  if (value === true)
    return (
      <span className="chip chip-warn" title="A 2D-proximity heuristic gives a different answer from the 3D truth">
        2D shortcut misleads
      </span>
    );
  if (value === false)
    return (
      <span className="chip" title="The 2D-proximity heuristic agrees with the 3D truth">
        No shortcut conflict
      </span>
    );
  return (
    <span className="chip chip-muted" title="Not a spatial family, or this run does not record the 2D shortcut check">
      Shortcut check n/a
    </span>
  );
}

function ModelRow({ m }: { m: ModelAnswer }) {
  return (
    <li className="model-row">
      <span className="model-row-name">
        {m.label}
        {!m.parse_ok && <span className="tag">unparsed</span>}
      </span>
      <span className="model-row-answer">{fmt(m.answer)}</span>
      <Mark ok={m.correct} />
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

function TaskCard({ scene, idx, setIdx }: { scene: SceneData; idx: number; setIdx: (i: number) => void }) {
  const task = scene.tasks[idx];
  const names = new Map(scene.objects.map((o) => [o.id, o.name]));
  if (!task) return <section className="card">No tasks in this scene.</section>;
  return (
    <section className="card task-card" aria-live="polite">
      <div className="stepper">
        <button className="step-btn" onClick={() => setIdx(Math.max(0, idx - 1))} disabled={idx === 0} aria-label="Previous task">
          ‹
        </button>
        <div className="step-dots">
          {scene.tasks.map((t, i) => {
            const m = t.models[0];
            const cls = m ? (m.correct ? "dot-ok" : "dot-bad") : "dot-none";
            return (
              <button
                key={t.task_id}
                className={`step-dot ${cls}${i === idx ? " current" : ""}`}
                onClick={() => setIdx(i)}
                aria-label={`Task ${i + 1}: ${familyLabel(t.family)}`}
                title={`${i + 1}. ${familyLabel(t.family)}`}
              />
            );
          })}
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

      <div className="truth">
        <span className="truth-label">True answer</span>
        <span className="truth-value">
          {task.answer_text}
          {task.tolerance !== null && task.tolerance !== undefined && <span className="tol"> ± {task.tolerance}</span>}
        </span>
      </div>

      <h3 className="section-title">Model answers</h3>
      {task.models.length ? (
        <ul className="models">{task.models.map((m) => <ModelRow key={m.label} m={m} />)}</ul>
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

// ---------------------------------------------------------------- page

export default function Explorer() {
  const [index, setIndex] = useState<ExplorerIndex | null>(null);
  const [error, setError] = useState<string | null>(null);
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

  const selectTask = useCallback(
    (sc: SceneData, i: number) => {
      const t = sc.tasks[i];
      setTaskIdx(i);
      const cam = (t && sc.cameras.find((c) => c.camera_id === t.camera_id)) ?? sc.cameras[0] ?? null;
      setCameraId(cam ? cam.camera_id : null);
      setMode("camera");
      setSnapKey((k) => k + 1);
      writeUrl(sc.scene_id, i);
    },
    [],
  );

  // When a scene arrives, open its first (or URL-requested) task.
  useEffect(() => {
    if (!scene || pendingTask === null) return;
    const i = Math.min(Math.max(pendingTask, 0), Math.max(scene.tasks.length - 1, 0));
    setPendingTask(null);
    setGltfFailed(false);
    selectTask(scene, i);
  }, [scene, pendingTask, selectTask]);

  const pickScene = (id: string) => {
    if (id === sceneId) return;
    setSceneId(id);
    setPendingTask(0);
  };

  const setIdx = useCallback((i: number) => scene && selectTask(scene, i), [scene, selectTask]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!scene || e.target instanceof HTMLInputElement || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "ArrowRight") setIdx(Math.min(scene.tasks.length - 1, taskIdx + 1));
      if (e.key === "ArrowLeft") setIdx(Math.max(0, taskIdx - 1));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [scene, taskIdx, setIdx]);

  const task = scene?.tasks[taskIdx];
  const cam = scene?.cameras.find((c) => c.camera_id === cameraId) ?? scene?.cameras[0] ?? null;
  const evidence = useMemo(() => new Set(task?.evidence_object_ids ?? []), [task]);
  const picks = useMemo(() => (scene ? wrongPicks(scene, task) : new Set<string>()), [scene, task]);
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
      <Header index={index} />
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
                    title={c ? `${c.camera_id} camera` : `No ${rig} camera in this run`}
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
                  <CameraImage scene={scene} cam={cam} evidence={task?.evidence_object_ids ?? []} picks={picks} />
                  <figcaption className="pane-tag">{cam.camera_id} image</figcaption>
                </figure>
              )}
              {layout !== "photo" && (
                <figure className="pane pane-3d" style={{ aspectRatio: aspect }}>
                  <SceneView3D
                    scene={scene}
                    dataBase={DATA}
                    camera={cam}
                    evidence={evidence}
                    picks={picks}
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
              <label>
                Labels{" "}
                <select value={labels} onChange={(e) => setLabels(e.target.value as LabelMode)}>
                  <option value="evidence">evidence objects</option>
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
                <span className="muted">3D from the scene file (primitives); no glTF in this run</span>
              )}
              <span className="muted legend">
                <span className="swatch" /> evidence for the question
                {picks.size > 0 && (
                  <>
                    <span className="swatch swatch-pick" /> a model&apos;s wrong pick
                  </>
                )}
              </span>
            </div>
          </section>

          <TaskCard scene={scene} idx={taskIdx} setIdx={setIdx} />
        </div>
      )}

      <footer className="page-foot">
        <p>
          Every true answer is computed by a program run on the scene file, never by a model or by hand. Gates G1 to G7
          are model-free checks; a task ships only if its gates pass.
        </p>
        <p className="muted">
          Run {index.source.run} · recipe {index.source.recipe ?? "—"} · seed {index.source.run_seed ?? "—"} · scene spec{" "}
          {index.source.spec_version ?? "—"} · {index.source.generator_version ?? ""}
        </p>
      </footer>
    </main>
  );
}
