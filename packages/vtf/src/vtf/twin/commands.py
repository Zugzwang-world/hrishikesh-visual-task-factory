"""`vtf twin <action>`: mat, shopping or layouts (called lazily from vtf.cli)."""

import json
from pathlib import Path

ACTIONS = ("mat", "shopping", "layouts")


def _selection(path: Path | None) -> dict | None:
    return json.loads(Path(path).read_text(encoding="utf-8")) if path else None


def run_twin(
    action: str,
    path: Path | None,
    out: Path,
    limit: int | None = None,
    selection: Path | None = None,
) -> list[Path]:
    """`selection` is a layout pick (docs/twins/selection.json: scene_ids, pieces_to_buy): the
    sheets print only those scenes and the shopping list buys only what they need."""
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
        return write_shopping_list(out, quantities=sel["pieces_to_buy"], scope=scope)
    if action == "layouts":
        if path is None:
            raise SystemExit(
                "vtf twin layouts: give the run folder, e.g. vtf twin layouts out/twins"
            )
        from vtf.twin.layouts import make_layouts

        pdf = Path(out) if Path(out).suffix == ".pdf" else Path(out) / "layouts.pdf"
        if sel is None:
            return [make_layouts(path, pdf, limit=limit)]
        return [
            make_layouts(path, pdf, scene_ids=sel["scene_ids"], quantities=sel["pieces_to_buy"])
        ]
    raise SystemExit(f"vtf twin: unknown action {action!r}; use one of {', '.join(ACTIONS)}")
