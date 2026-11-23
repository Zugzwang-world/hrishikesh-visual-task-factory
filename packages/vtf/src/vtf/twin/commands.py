"""`vtf twin <action>`: mat, shopping or layouts (called lazily from vtf.cli)."""

from pathlib import Path

ACTIONS = ("mat", "shopping", "layouts")


def run_twin(action: str, path: Path | None, out: Path, limit: int | None = None) -> list[Path]:
    if action == "mat":
        from vtf.twin.mat import make_mat

        return make_mat(out)
    if action == "shopping":
        from vtf.twin.shopping import write_shopping_list

        return write_shopping_list(out)
    if action == "layouts":
        if path is None:
            raise SystemExit(
                "vtf twin layouts: give the run folder, e.g. vtf twin layouts out/twins"
            )
        from vtf.twin.layouts import make_layouts

        pdf = Path(out) if Path(out).suffix == ".pdf" else Path(out) / "layouts.pdf"
        return [make_layouts(path, pdf, limit=limit)]
    raise SystemExit(f"vtf twin: unknown action {action!r}; use one of {', '.join(ACTIONS)}")
