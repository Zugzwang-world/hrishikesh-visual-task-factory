"""`vtf twin <action>` (called lazily from vtf.cli).

    mat          placement mat PDFs and mat_spec.json           -> docs/twins/
    shopping     shopping list                                   -> docs/twins/
    layouts      layout sheets of a run                          -> docs/twins/layouts.pdf
    texture      the A3 mat as the renderer's table texture      -> docs/twins/mat_texture.png
    guide        photo-session guide, markdown and PDF           -> docs/twins/photo_session.*
    digital      digital-twin scene files (+ misplaced control)  -> out/twins_digital
    check        G8 on the rendered digital twins                -> docs/twins/g8_digital_check.md
    register     photos in an inbox -> G8, sibling renders, tasks -> out/twins_real
    sim-to-real  accuracy on photos vs their synthetic siblings  -> <run>/sim_to_real/
"""

import json
from pathlib import Path

ACTIONS = ("mat", "shopping", "layouts", "texture", "guide", "digital", "check", "register",
           "sim-to-real")  # fmt: skip
DOCS = Path("docs/twins")
DEFAULT_SELECTION = DOCS / "selection.json"
DEFAULT_OUT = {
    "layouts": DOCS / "layouts.pdf",
    "digital": Path("out/twins_digital"),
    "register": Path("out/twins_real"),
}


def _selection(path: Path | None) -> dict | None:
    return json.loads(Path(path).read_text(encoding="utf-8")) if path else None


def _need(path: Path | None, action: str, example: str) -> Path:
    if path is None:
        raise SystemExit(f"vtf twin {action}: give the folder, e.g. vtf twin {action} {example}")
    return Path(path)


def run_twin(
    action: str,
    path: Path | None,
    out: Path | None = None,
    limit: int | None = None,
    selection: Path | None = None,
    twins: Path | None = None,
    render: bool = True,
    device: str = "gpu",
    models: list[str] | None = None,
    focal_35mm: float | None = None,
) -> list[Path] | dict:
    """`selection` is a layout pick (docs/twins/selection.json: scene_ids, pieces_to_buy): the
    sheets print only those scenes and the shopping list buys only what they need."""
    out = Path(out) if out is not None else DEFAULT_OUT.get(action, DOCS)
    if action in ("guide", "digital", "check", "register") and selection is None:
        selection = DEFAULT_SELECTION
    sel = _selection(selection)
    if action == "mat":
        from vtf.twin.mat import make_mat

        return make_mat(out)
    if action == "shopping":
        from vtf.twin.shopping import write_shopping_list

        if sel is None:
            return write_shopping_list(out)
        scope = (
            f"Only what the {len(sel['scene_ids'])} layouts in layouts.pdf need "
            f"({', '.join(sel['scene_ids'])}; chosen in selection.json)."
        )
        hero = sel.get("hero") or {}
        new = {a: int(n) for a, n in (hero.get("new_pieces") or {}).items() if n}
        if not hero.get("layouts"):
            return write_shopping_list(out, quantities=sel["pieces_to_buy"], scope=scope)
        quantities = dict(sel["pieces_to_buy"])
        for a, n in new.items():
            quantities[a] = quantities.get(a, 0) + n
        scope += (
            f" Plus the {len(hero['layouts'])} hero layouts in hero_layouts.pdf "
            f"({', '.join(h['label'] for h in hero['layouts'])}), shot first."
        )
        return write_shopping_list(out, quantities=quantities, scope=scope, new=new)
    if action == "layouts":
        path = _need(path, action, "out/twins")
        from vtf.twin.layouts import make_layouts

        pdf = Path(out) if Path(out).suffix == ".pdf" else Path(out) / "layouts.pdf"
        if sel is None:
            return [make_layouts(path, pdf, limit=limit)]
        return [
            make_layouts(path, pdf, scene_ids=sel["scene_ids"], quantities=sel["pieces_to_buy"])
        ]
    if action == "texture":
        from vtf.twin.digital import TEXTURE_FILE, write_mat_texture
        from vtf.twin.mat import A3_FILE

        return [write_mat_texture(Path(path or DOCS / A3_FILE), Path(out) / TEXTURE_FILE)]
    if action == "guide":
        from vtf.twin.guide import make_guide

        return make_guide(Path(path or "out/twins"), sel, out)  # type: ignore[arg-type]
    if action == "digital":
        from vtf.twin.digital import make_digital_twins

        return make_digital_twins(Path(path or "out/twins"), sel, out)  # type: ignore[arg-type]
    if action == "check":
        from vtf.twin.check import digital_check

        doc = Path(out) / "g8_digital_check.md" if Path(out) == DOCS else Path(out)
        return digital_check(Path(path or DEFAULT_OUT["digital"]), sel, doc)  # type: ignore
    if action == "register":
        from vtf.twin.real import register_inbox

        return register_inbox(
            Path(path or "twins/inbox"), sel, Path(twins or DEFAULT_OUT["digital"]), out,  # type: ignore
            render=render, device=device, focal_35mm=focal_35mm,
        )  # fmt: skip
    if action == "sim-to-real":
        from vtf.twin.sim_to_real import sim_to_real

        return sim_to_real(Path(path or DEFAULT_OUT["register"]), models or ["fake-oracle"])
    raise SystemExit(f"vtf twin: unknown action {action!r}; use one of {', '.join(ACTIONS)}")
