"""Shopping list for physical twins: 15 common items, each standing in for one manifest asset.

Every item is an asset in assets/manifest.yaml that a person can buy in one supermarket trip and
that fits the mat's placement area (31 x 19 cm, `vtf.twin.mat`). The target size is the asset's
`size_m`; anything within +-20 % per dimension is fine. The colour is the manifest colour word,
because questions name objects by it ("the red tin").

Left out on purpose: trays, plates, baskets, the large bowl, jugs and the cheese box (wider than
the placement area is deep), the wooden spoon and croissant (too long to place freely), tools and
figurines (not kitchen items), the baseball and lychee (hard to find in an ordinary supermarket).

Output: docs/twins/shopping_list.md (plain list for DEMO.md) and shopping_list.json (asset ids,
quantities and size ranges, for the twin recipe and the layout sheets).
"""

import json
from dataclasses import dataclass
from pathlib import Path

from vtf.core.assets import AssetManifest, load_asset_manifest

SHOPPING_VERSION = "0.2.0"
TOLERANCE = 0.20


@dataclass(frozen=True)
class Candidate:
    asset_id: str
    item: str  # list heading, e.g. "Lemons"
    hint: str  # what exactly to buy
    quantity: int  # copies, for count questions and look-alikes
    long_word: str = "long"  # "tall" for upright things, "wide" for a tape roll


# Order is the order of the printed list. Quantities above one give count questions something to
# count; the two citrus fruits, the two red fruits, the two bottles and the two aerosol cans are
# the manifest's look-alike groups.
CANDIDATES: tuple[Candidate, ...] = (
    Candidate("food_apple_01", "Apples", "large red apples", 2),
    Candidate("food_pomegranate_01", "Pomegranate", "a pomegranate", 1),
    Candidate("lemon", "Lemons", "lemons", 3),
    Candidate("food_lime_01", "Limes", "limes", 3),
    Candidate("food_avocado_01", "Avocado", "a large avocado", 1),
    Candidate("food_kiwi_01", "Kiwis", "large kiwi fruit", 2),
    Candidate("yellow_onion", "Onions", "brown onions", 2),
    Candidate("wooden_bowl_02", "Small bowl", "a small bowl, wooden if possible", 1, "tall"),
    Candidate("cleaner_tin_01", "Tins", "food tins with a red label and a flat lid", 2, "tall"),
    Candidate("leather_cleaner_can", "Blue aerosol can", "a dark blue spray can (deodorant, "
              "cooking spray)", 1, "tall"),
    Candidate("lubricant_spray", "Silver aerosol can", "a plain silver or grey spray can (no "
              "strong colour: questions call it just \"can\")", 1, "tall"),
    Candidate("multi_cleaner_bottle", "Spray bottle", "a white trigger spray bottle "
              "(kitchen cleaner)", 1, "tall"),
    Candidate("bleach_bottle", "Purple bottle", "a purple plastic bottle (fabric softener, "
              "detergent or bleach; questions call it the purple bottle)", 1, "tall"),
    Candidate("medical_tape", "Tape roll", "a small white roll of medical or masking tape", 1,
              "wide"),
    Candidate("rubber_duck_toy", "Rubber duck", "a yellow rubber duck (baby aisle)", 1),
)  # fmt: skip


@dataclass(frozen=True)
class ShoppingItem:
    asset_id: str
    item: str
    hint: str
    quantity: int
    category: str
    colour: str | None
    size_m: tuple[float, float, float]
    long_word: str

    def ranges_m(self) -> list[tuple[float, float]]:
        return [(v * (1 - TOLERANCE), v * (1 + TOLERANCE)) for v in self.size_m]

    def size_text(self) -> str:
        """Plain words in the asset's frame (z is up as the asset is modelled)."""
        x, y, z = self.size_m
        across = max(x, y)

        def cm(v: float) -> str:
            return f"{v * 100:.1f}".rstrip("0").rstrip(".")

        def rng(v: float) -> str:
            return f"{cm(v * (1 - TOLERANCE))} to {cm(v * (1 + TOLERANCE))} cm"

        if max(x, y, z) <= 1.12 * min(x, y, z):  # round: pomegranate
            return f"about {cm(across)} cm across (fine from {rng(across)})"
        if max(x, y) <= 1.12 * min(x, y):  # round footprint: fruit, bowl, tins, bottles, tape
            word = self.long_word if z > across or self.long_word == "wide" else "tall"
            return (
                f"about {cm(across)} cm across and {cm(z)} cm {word} "
                f"(fine from {rng(across)} across, {rng(z)} {word})"
            )
        return (
            f"about {cm(z)} cm tall on a {cm(across)} x {cm(min(x, y))} cm footprint "
            f"(fine from {rng(z)} tall, each side within a fifth)"
        )


def build_shopping_list(
    manifest: AssetManifest | None = None, quantities: dict[str, int] | None = None
) -> list[ShoppingItem]:
    """Resolve every candidate against the manifest. Missing assets raise: the list must only
    name things the factory can render. `quantities` (asset id -> pieces, from a layout
    selection) keeps only those candidates, with those counts."""
    manifest = manifest or load_asset_manifest()
    if quantities is not None:
        unknown = set(quantities) - {c.asset_id for c in CANDIDATES}
        if unknown:
            raise ValueError(f"not on the candidate list: {sorted(unknown)}")
    items = []
    for c in CANDIDATES:
        if quantities is not None and not quantities.get(c.asset_id):
            continue
        entry = manifest.get(c.asset_id)
        items.append(
            ShoppingItem(
                asset_id=entry.id,
                item=c.item,
                hint=c.hint,
                quantity=c.quantity if quantities is None else int(quantities[c.asset_id]),
                category=entry.category,
                colour=entry.color_name,
                size_m=tuple(entry.size_m),
                long_word=c.long_word,
            )
        )
    return items


def shopping_markdown(
    items: list[ShoppingItem], scope: str | None = None, new: dict[str, int] | None = None
) -> str:
    """`new` (asset id -> pieces) marks what was added to an earlier list, e.g. for the hero
    layouts."""
    new = new or {}
    total = sum(i.quantity for i in items)
    lines = [
        "# Physical-twin shopping list",
        "",
        f"{len(items)} kinds of item, {total} pieces in all, one supermarket trip. Each stands "
        "in for one 3D asset. Sizes are the asset's real size; anything within a fifth either "
        "way is fine. Pick plain items close to the colour given, because questions name objects "
        "by colour.",
        "",
    ]
    if scope:
        lines[2:2] = [scope, ""]
    if new:
        lines[2:2] = [f"**New for the hero layouts:** {sum(new.values())} pieces, marked NEW "
                      "below. Everything else is the earlier list, unchanged.", ""]  # fmt: skip
    for n, it in enumerate(items, 1):
        colour = f" ({it.colour})" if it.colour else ""
        k = new.get(it.asset_id, 0)
        mark = "" if not k else (" NEW" if k == it.quantity else f" ({k} NEW)")
        lines.append(
            f"{n}. **{it.item} x{it.quantity}**{mark}{colour}: {it.hint}; {it.size_text()}. "
            f"Asset `{it.asset_id}`."
        )
    lines += [
        "",
        "Also: the printed mat (docs/twins/mat_A3.pdf, or the four A4 tiles of "
        "mat_A4_tiled.pdf), sticky tape, and a phone stand or a stack of books for the camera "
        "spot.",
        "",
        "Not on the list: trays, plates, baskets and the big bowl are wider than the mat's "
        "placement area is deep (19 cm); the wooden spoon and the croissant are too long.",
    ]
    return "\n".join(lines) + "\n"


def shopping_json(items: list[ShoppingItem], new: dict[str, int] | None = None) -> dict:
    new = new or {}
    return {
        "shopping_version": SHOPPING_VERSION,
        "tolerance": TOLERANCE,
        "items": [
            {
                "asset_id": it.asset_id,
                "item": it.item,
                "hint": it.hint,
                "quantity": it.quantity,
                "category": it.category,
                "colour": it.colour,
                "size_m": list(it.size_m),
                "range_m": [[round(lo, 4), round(hi, 4)] for lo, hi in it.ranges_m()],
                **({"new": new[it.asset_id]} if new.get(it.asset_id) else {}),
            }
            for it in items
        ],
    }


def write_shopping_list(
    out: Path,
    manifest: AssetManifest | None = None,
    quantities: dict[str, int] | None = None,
    scope: str | None = None,
    new: dict[str, int] | None = None,
) -> list[Path]:
    """`vtf twin shopping`: shopping_list.md and .json (out is a folder or a .md path).
    `quantities` limits the list to what a layout selection needs; `new` marks added pieces."""
    out = Path(out)
    md = out if out.suffix == ".md" else out / "shopping_list.md"
    md.parent.mkdir(parents=True, exist_ok=True)
    items = build_shopping_list(manifest, quantities)
    md.write_text(shopping_markdown(items, scope, new), encoding="utf-8", newline="\n")
    js = md.with_suffix(".json")
    data = shopping_json(items, new)
    if quantities is not None:
        data["scope"] = scope
    js.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"vtf twin shopping: {len(items)} items -> {md}, {js.name}")
    return [md, js]
