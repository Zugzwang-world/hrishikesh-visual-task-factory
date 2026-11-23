// Which objects to mark for a task, in what role, with what words. Shared by the camera image,
// the 3D view and the gallery cards, so a role always has the same colour and the same label.
//
// Colours are colour-blind safe as a pair (blue truth, orange 2D shortcut, as in the headline
// chart), and every mark also carries words, so identity never rests on colour alone. A model's
// wrong pick is reddish purple; plain evidence is neutral (white with a dark halo).

import type { SceneData, TaskData } from "./types";

export type Role = "truth" | "shortcut" | "pick" | "evidence";

export const ROLE_COLOR: Record<Role, string> = {
  truth: "#2a78d6",
  shortcut: "#eb6834",
  pick: "#a0458f",
  evidence: "#ffffff",
};

export const ROLE_WORDS: Record<Role, string> = {
  truth: "true answer",
  shortcut: "2D shortcut",
  pick: "wrong pick",
  evidence: "evidence",
};

export interface Highlights {
  role: Map<string, Role>;
  label: Map<string, string>; // object id -> label text
  always: Set<string>; // labelled even when evidence labels are hidden
  boolTruth: boolean; // a yes/no answer: blue marks the objects the answer rests on
}

const EMPTY: Highlights = { role: new Map(), label: new Map(), always: new Set(), boolTruth: false };

/** Objects named by image models' wrong answers (object and set answers). */
export function wrongPicks(scene: SceneData, task: TaskData | undefined): Set<string> {
  const out = new Set<string>();
  if (!task || (task.answer_type !== "object" && task.answer_type !== "set")) return out;
  for (const m of task.models) {
    if (m.correct || m.with_image === false) continue;
    if (m.answer_object_ids) {
      m.answer_object_ids.forEach((id) => out.add(id));
      continue;
    }
    // v0.1 exports: match the answer text to an object's full name.
    if (typeof m.answer !== "string") continue;
    const said = m.answer.trim().toLowerCase().replace(/^the\s+/, "");
    for (const o of scene.objects) if (o.name.toLowerCase() === said) out.add(o.id);
  }
  return out;
}

export function taskHighlights(scene: SceneData | undefined, task: TaskData | undefined): Highlights {
  if (!scene || !task) return EMPTY;
  const names = new Map(scene.objects.map((o) => [o.id, o.name]));
  const role = new Map<string, Role>();
  const boolTruth = task.answer_type === "bool";
  task.evidence_object_ids.forEach((id) => role.set(id, "evidence"));
  wrongPicks(scene, task).forEach((id) => role.set(id, "pick"));
  if (task.shortcut_conflict) {
    (task.shortcut_object_ids ?? []).forEach((id) => role.set(id, "shortcut"));
    (task.truth_object_ids ?? []).forEach((id) => role.set(id, "truth"));
  }
  const label = new Map<string, string>();
  const always = new Set<string>();
  for (const [id, r] of role) {
    const name = names.get(id) ?? id;
    if (r === "evidence" || (r === "truth" && boolTruth)) {
      label.set(id, name);
    } else {
      label.set(id, `${ROLE_WORDS[r]} · ${name}`);
      always.add(id);
    }
  }
  return { role, label, always, boolTruth };
}

/** An answer value as words: yes/no, "none" for an empty set, lists joined. */
export function answerText(v: unknown): string {
  if (v === true) return "yes";
  if (v === false) return "no";
  if (v === null || v === undefined || v === "") return "—";
  if (Array.isArray(v)) return v.length ? v.map(answerText).join(", ") : "none";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}
