# /// script
# requires-python = ">=3.12"
# dependencies = ["python-pptx>=1.0.2", "pillow>=10"]
# ///
# ruff: noqa: E501  (slide and caption copy is prose)
"""The submission deck on the organizers' mandatory template (brief VTF-09, section 2).

    uv run scripts/build_slides.py            # writes docs/slides/vtf_slides.pptx
    uv run scripts/render_slides.py           # PNG per slide and the PDF (see that script)

Starts from the organizers' Google Slides template, exported as PowerPoint
(docs/slides/template/innodata_template.pptx): its title card, its content-page design (black
header with the Innodata and H2S logos, gradient bar at the foot) and its thank-you card. The
template's own section prompts (brief about the idea, opportunity, features, process flow,
technologies, cost) appear as the small blue kicker above each title.

Every number comes from scripts/demo_numbers.py: frozen v1 numbers from findings.json, and the
three Phase 2 results ({{AUDIT}}, {{SIM2REAL}}, {{REAL_TRAP}}) left as orange placeholders until
their outputs exist. scripts/refresh_numbers.py rebuilds this deck with them filled in.
"""

from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from demo_numbers import ROOT, numbers  # noqa: E402

TEMPLATE = ROOT / "docs/slides/template/innodata_template.pptx"
OUT = ROOT / "docs/slides/vtf_slides.pptx"
MEDIA = ROOT / "docs/media"
PACK = MEDIA / "pack"

HEAD, BODY, MONO = "League Spartan Medium", "League Spartan", "Courier New"
INK = RGBColor(0x0F, 0x10, 0x16)
GREY = RGBColor(0x55, 0x5B, 0x66)
LIGHT = RGBColor(0xF2, 0xF4, 0xF7)
LINE = RGBColor(0xD9, 0xDE, 0xE5)
BLUE = RGBColor(0x17, 0x6E, 0xB8)  # template bar start
ORANGE = RGBColor(0xDC, 0x83, 0x35)  # template bar end
TRUTH = RGBColor(0x2A, 0x78, 0xD6)  # Explorer "true answer"
SHORTCUT = RGBColor(0xEB, 0x68, 0x34)  # Explorer "2D shortcut"
PH = RGBColor(0xD9, 0x3B, 0x00)  # unfilled placeholder
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
TERM_BG = RGBColor(0x14, 0x17, 0x1F)

L, R = 0.40, 9.60
W = R - L
REPO = "github.com/Zugzwang-world/hrishikesh-visual-task-factory"
EXPLORER = "vtf-task-explorer.vercel.app"
DATASET = "huggingface.co/datasets/Hrishi1802/hrishikesh-visual-task-factory"

TOKEN = re.compile(r"(\*\*.+?\*\*|\^\^.+?\^\^|~~.+?~~|\{\{[A-Z0-9_]+\}\})")


class Deck:
    def __init__(self, n: dict[str, str | None], h1_photo: Path | None = None) -> None:
        self.n = n
        self.h1_photo = h1_photo
        self.prs = Presentation(str(TEMPLATE))
        content = self.prs.slides[1]
        self.bg_blob = next(s for s in content.shapes if s.shape_type == 13).image.blob
        self.bar = copy.deepcopy(
            next(s for s in content.shapes if s.name.endswith(";66;p15"))._element
        )
        self.blank = next(lay for lay in self.prs.slide_layouts if lay.name == "BLANK")
        # keep the title card (first) and the thank-you card (last); drop the template's examples
        ids = self.prs.slides._sldIdLst
        for sld in list(ids)[1:-1]:
            self.prs.part.drop_rel(sld.get(qn("r:id")))
            ids.remove(sld)

    # ------------------------------------------------------------------ text

    def fill(self, text: str) -> str:
        def sub(m: re.Match) -> str:
            v = self.n.get(m.group(1))
            return v if v is not None else m.group(0)

        return re.sub(r"\{\{([A-Z0-9_]+)\}\}", sub, text)

    def runs(self, p, text: str, size: float, color=INK, font=BODY, bold=False) -> None:
        for part in TOKEN.split(self.fill(text)):
            if not part:
                continue
            r = p.add_run()
            f = r.font
            f.size, f.name, f.bold = Pt(size), font, bold
            f.color.rgb = color
            if part.startswith("**"):
                r.text, f.name = part[2:-2], HEAD
            elif part.startswith("^^"):
                r.text, f.name = part[2:-2], HEAD
                f.color.rgb = BLUE
            elif part.startswith("~~"):
                r.text, f.name = part[2:-2], HEAD
                f.color.rgb = ORANGE
            elif part.startswith("{{"):
                r.text, f.name, f.bold = part, HEAD, True  # an unfilled Phase 2 placeholder
                f.color.rgb = PH
            else:
                r.text = part

    def text(self, slide, x, y, w, h, paras, size=12.0, color=INK, font=BODY, align=PP_ALIGN.LEFT,
             anchor=MSO_ANCHOR.TOP, bullets=False, space=4.0, line=1.0, name=None, bold=False):  # fmt: skip
        box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        if name:
            box.name = name
        tf = box.text_frame
        tf.word_wrap = True
        tf.auto_size = None
        tf.vertical_anchor = anchor
        for side in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
            setattr(tf, side, Inches(0.03))
        if isinstance(paras, str):
            paras = [paras]
        for i, para in enumerate(paras):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.alignment = align
            p.line_spacing = line
            p.space_after = Pt(space)
            psize = size
            if isinstance(para, tuple):  # (text, size)
                para, psize = para
            if bullets:
                pPr = p._p.get_or_add_pPr()
                pPr.set("marL", str(Emu(Inches(0.17))))
                pPr.set("indent", str(-Emu(Inches(0.17))))
                bu_clr = pPr.makeelement(qn("a:buClr"), {})
                clr = bu_clr.makeelement(qn("a:srgbClr"), {"val": "176EB8"})
                bu_clr.append(clr)
                pPr.append(bu_clr)
                pPr.append(pPr.makeelement(qn("a:buFont"), {"typeface": "Arial"}))
                pPr.append(pPr.makeelement(qn("a:buChar"), {"char": "•"}))
            self.runs(p, para, psize, color, font, bold)
        return box

    def rect(self, slide, x, y, w, h, fill=LIGHT, line=None, shape=MSO_SHAPE.ROUNDED_RECTANGLE,
             radius=0.08, name=None):  # fmt: skip
        s = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
        if name:
            s.name = name
        if fill is None:
            s.fill.background()
        else:
            s.fill.solid()
            s.fill.fore_color.rgb = fill
        if line is None:
            s.line.fill.background()
        else:
            s.line.color.rgb = line
            s.line.width = Pt(1)
        if shape == MSO_SHAPE.ROUNDED_RECTANGLE:
            s.adjustments[0] = radius
        s.shadow.inherit = False
        return s

    def picture(self, slide, path: Path, x, y, w, h, align="center"):
        iw, ih = Image.open(path).size
        scale = min(w / iw, h / ih)
        pw, ph = iw * scale, ih * scale
        px = x + (w - pw) / 2 if align == "center" else x
        py = y + (h - ph) / 2
        return slide.shapes.add_picture(str(path), Inches(px), Inches(py), Inches(pw), Inches(ph))

    # ------------------------------------------------------------------ pages

    def page(self, kicker: str, title: str, notes: str = ""):
        s = self.prs.slides.add_slide(self.blank)
        s.shapes.add_picture(
            _blob_path(self.bg_blob), 0, 0, self.prs.slide_width, self.prs.slide_height
        )
        s.shapes._spTree.append(copy.deepcopy(self.bar))
        self.text(s, L, 0.70, W, 0.26, kicker.upper(), size=9.5, color=BLUE, font=HEAD, space=0)
        self.text(s, L, 0.92, W, 0.52, title, size=21, font=HEAD, space=0, anchor=MSO_ANCHOR.TOP)
        if notes:
            s.notes_slide.notes_text_frame.text = self.fill(notes)
        return s

    def card(self, s, x, y, w, h, head, body, accent=BLUE, size=11.0, head_size=12.5, fill=LIGHT):
        self.rect(s, x, y, w, h, fill=fill)
        self.rect(s, x, y + 0.1, 0.06, h - 0.2, fill=accent, shape=MSO_SHAPE.RECTANGLE)
        self.text(s, x + 0.16, y + 0.08, w - 0.26, 0.32, head, size=head_size, font=HEAD, space=0)
        self.text(s, x + 0.16, y + 0.40, w - 0.26, h - 0.46, body, size=size, color=GREY, space=3,
                  line=1.05)  # fmt: skip

    def build(self) -> Presentation:
        self.title_card()
        self.stakes()
        self.why_3d()
        self.pipeline()
        self.validation()
        self.variation()
        self.headline()
        self.supporting()
        self.physical_twins()
        self.human_baseline()
        self.repeatability()
        self.impact()
        ids = self.prs.slides._sldIdLst  # the thank-you card goes last
        thanks = ids[1]
        ids.remove(thanks)
        ids.append(thanks)
        # new slides reuse the dropped examples' part names; renumber so every part name is unique
        self.prs.part.rename_slide_parts([sld.get(qn("r:id")) for sld in ids])
        return self.prs

    def title_card(self) -> None:
        s = self.prs.slides[0]
        fields = {"Team Name": "{{TEAM_NAME}}", "Team Leader Name": "{{TEAM_LEADER}}",
                  "Problem Statement": "Challenge 02, Visual Task Factory"}  # fmt: skip
        for sh in s.shapes:
            if not sh.has_text_frame:
                continue
            label = sh.text_frame.text.strip().rstrip(":").strip()
            if label in fields:
                p = sh.text_frame.paragraphs[0]
                base = p.runs[0].font
                size, color = base.size.pt if base.size else 18, base.color.rgb
                for r in list(p.runs)[1:]:
                    p._p.remove(r._r)
                p.runs[0].text = f"{label} : "
                self.runs(p, fields[label], size, color, BODY)
        self.text(s, 5.15, 3.28, 4.45, 0.62, "Visual Task Factory", size=24, font=HEAD,
                  align=PP_ALIGN.RIGHT, space=0)  # fmt: skip
        self.text(s, 5.45, 3.9, 4.15, 1.0,
                  "A 3D robot workcell: photoreal scenes, the spatial questions a robot must get "
                  "right, every answer proven from the scene file with no AI in the checking, and "
                  "the Claude models we tested measured against them.",
                  size=11, color=GREY, align=PP_ALIGN.RIGHT, line=1.05, space=0)  # fmt: skip
        s.notes_slide.notes_text_frame.text = (
            "Visual Task Factory. A factory of 3D robot-workcell tasks with exact answers and no AI "
            "in the checking, and what it finds in the Claude models we tested."
        )

    def stakes(self) -> None:
        s = self.page("Brief about the idea", "Robots now act on what a vision model tells them",
                      "Stakes: a robot asks a vision-language model where things are, then moves. "
                      "In this scene every Claude setting we tested names the box; the jug is "
                      "nearer in 3D. That is a wrong grasp.")  # fmt: skip
        self.picture(s, PACK / "stakes_front.jpg", L, 1.55, 4.75, 3.56)
        self.rect(
            s, L, 1.55, 4.75, 0.42, fill=RGBColor(0x0F, 0x10, 0x16), shape=MSO_SHAPE.RECTANGLE
        )
        self.text(s, L + 0.1, 1.58, 4.6, 0.36, "Robot asks: “Which object is nearest to the gripper's tip?”",
                  size=11.5, color=WHITE, font=HEAD, anchor=MSO_ANCHOR.MIDDLE, space=0)  # fmt: skip
        x = 5.4
        self.text(s, x, 1.55, R - x, 1.5, [
            ("In the image the ~~box~~ looks nearest. In 3D it is the ^^jug^^. "
             "Every Claude setting we tested answered the box.", 13.5),
            ("A wrong spatial answer is a wrong grasp, a collision or a missed item.", 12),
        ], space=8, line=1.05)  # fmt: skip
        self.card(s, x, 3.2, R - x, 1.8, "Our idea: a visual task factory for robot perception", [
            "Generates photoreal 3D robot-workcell scenes in Blender, seen by three robot cameras",
            "Asks the spatial questions a robot must get right, with answers proven from the scene file",
            "Finds where frontier vision models fail, and tests it on real photos",
        ], size=10.5)  # fmt: skip

    def why_3d(self) -> None:
        s = self.page("Opportunity: how we differ, and our USP", "One dimension further than 2D entries",
                      "Most entries generate charts and documents. We generate a world we fully "
                      "know, so depth, distance, occlusion and reach are exact.")  # fmt: skip
        rows = [
            ("", "Most entries (2D)", "Our factory (3D robot workcell)"),
            ("Image", "Charts, documents and diagrams",
             "A world we fully know, seen by three robot cameras on a tabletop"),
            ("Truth", "Depth, distance, occlusion and reach can't be measured",
             "All exact: physics-settled scenes and typed programs on the scene file"),
            ("Checking", "Answers checked by metadata or by a model",
             "Every answer proven twice without AI: the scene file, and the rendered pixels"),
            ("Variation", "Swapping colour or text",
             "Clutter, stacks, containers, occlusion, camera quality and three viewpoints"),
            ("Realism", "Synthetic only", "Physical twins: real objects on a measured mat, real photos with exact answers"),
            ("Output", "A benchmark at the end", "A finding with intervals and confound checks, plus risk cards"),
        ]  # fmt: skip
        gf = s.shapes.add_table(len(rows), 3, Inches(L), Inches(1.55), Inches(W), Inches(3.3))
        t = gf.table
        t.first_row, t.horz_banding = True, False
        for j, wdt in enumerate((1.25, 3.35, 4.6)):
            t.columns[j].width = Inches(wdt)
        for i, row in enumerate(rows):
            t.rows[i].height = Inches(0.42 if i else 0.36)
            for j, val in enumerate(row):
                c = t.cell(i, j)
                c.margin_left = c.margin_right = Inches(0.08)
                c.margin_top = c.margin_bottom = Inches(0.04)
                c.vertical_anchor = MSO_ANCHOR.MIDDLE
                c.fill.solid()
                head = i == 0
                c.fill.fore_color.rgb = (INK if head else (RGBColor(0xEA, 0xF2, 0xFB) if j == 2 else
                                         (WHITE if j == 1 else LIGHT)))  # fmt: skip
                tf = c.text_frame
                tf.word_wrap = True
                p = tf.paragraphs[0]
                color = WHITE if head else (INK if j != 1 else GREY)
                self.runs(p, val, 11 if head else 10.5, color, HEAD if (head or j == 0) else BODY)
        self.text(s, L, 4.93, W, 0.42,
                  "Prior art, stated plainly: CLEVR (2017) rendered 3D toy shapes with program answers. "
                  "We aim it at robots: real objects, robot cameras, robot questions, physical twins, "
                  "an evaluation harness.", size=9.5, color=GREY, space=0)  # fmt: skip

    def pipeline(self) -> None:
        s = self.page("Process flow, architecture and technologies",
                      "The scene file is the truth; images and answers both come from it",
                      "A seeded recipe makes a scene file. Blender renders it; programs answer from "
                      "it; eight model-free gates check both. Nothing in the answer path is a model.")  # fmt: skip
        steps = [
            ("Recipe", "YAML: families, clutter, cameras, difficulty"),
            ("Sampler", "Seeded scene JSON: objects, poses, zones"),
            ("Blender 5.3", "Physics settle; image, mask, depth per camera"),
            ("Programs", "12 question families, typed answers"),
            ("Gates G1–G8", "Model-free checks, every drop logged"),
            ("Task package", "Image, question, answer, checker, evidence"),
        ]
        bw, gap, y, h = 1.36, 0.208, 1.58, 1.12
        for i, (head, sub) in enumerate(steps):
            x = L + i * (bw + gap)
            accent = ORANGE if head.startswith("Gates") else BLUE
            self.rect(s, x, y, bw, h, fill=LIGHT)
            self.rect(s, x, y, bw, 0.07, fill=accent, shape=MSO_SHAPE.RECTANGLE)
            self.text(s, x + 0.07, y + 0.14, bw - 0.14, 0.3, head, size=12, font=HEAD, space=0,
                      align=PP_ALIGN.CENTER)  # fmt: skip
            self.text(s, x + 0.07, y + 0.46, bw - 0.14, h - 0.5, sub, size=9.5, color=GREY,
                      align=PP_ALIGN.CENTER, space=0, line=1.12)  # fmt: skip
            if i < len(steps) - 1:
                a = s.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, Inches(x + bw + 0.03), Inches(y + h / 2 - 0.09),
                                       Inches(gap - 0.06), Inches(0.18))  # fmt: skip
                a.fill.solid()
                a.fill.fore_color.rgb = GREY
                a.line.fill.background()
        outs = [
            ("Eval harness", "Claude API, batched; typed scorer → findings, risk cards"),
            ("Task Explorer", "3D viewer: rotate, switch cameras, see every answer"),
            ("Dataset", "v1 on Hugging Face: 3,181 tasks, CC0"),
            ("Robot learning", "Training data and verifiable rewards"),
        ]
        y2, ow, og = 3.08, 2.21, 0.123
        self.text(s, L, y2 - 0.02, W, 0.26, "THEN", size=8.5, color=GREY, font=HEAD, space=0)
        for i, (head, sub) in enumerate(outs):
            x = L + i * (ow + og)
            self.rect(s, x, y2 + 0.24, ow, 0.86, fill=RGBColor(0xEA, 0xF2, 0xFB))
            self.text(s, x + 0.1, y2 + 0.3, ow - 0.2, 0.28, head, size=11.5, font=HEAD, space=0)
            self.text(
                s, x + 0.1, y2 + 0.58, ow - 0.2, 0.5, sub, size=9.5, color=GREY, space=0, line=1.12
            )
        self.text(s, L, 4.32, W, 1.0, [
            "**Rules that hold it together:** answers come only from programs on the scene file; no "
            "AI image generator anywhere; the core never imports Blender, which talks to it through "
            "files; every generator is seeded, so a task ID is a content hash.",
            "**Built with:** Python 3.12, pydantic, NumPy and OpenCV, Blender 5.3 (Cycles), Poly Haven "
            "CC0 assets, Next.js and three.js on Vercel, the Anthropic Message Batches API, Hugging "
            "Face, uv.",
        ], size=10, color=INK, space=5, line=1.05)  # fmt: skip

    def validation(self) -> None:
        s = self.page("Features: validation evidence", "Every answer proven twice, with no AI in the checking",
                      "The funnel on the left is frozen v1. Every drop has a logged reason. G2 "
                      "recomputes each answer from the rendered pixels; G6 moves one object and "
                      "requires the answer to change.")  # fmt: skip
        self.picture(s, PACK / "gate_funnel.png", L, 1.6, 4.3, 3.0)
        self.text(s, L, 4.7, 4.3, 0.6, "From 7,013 candidate questions to 3,181 shipped tasks. "
                  "4,352 drops, each logged with its reason.", size=10, color=GREY, space=0)  # fmt: skip
        gates = [
            "**G1 Truth:** programs on the scene file, never a model (100%)",
            "**G2 Second computation:** recomputed from the rendered mask and depth pixels; must "
            "agree (95.4%; 167 disagreements rejected)",
            "**G3 Visible:** the evidence is in the image (99.5%)",
            "**G4 No near-ties** in depth or distance",
            "**G5 Needs the image:** Sonnet without it scores 15%, below the 25% always-guess baseline",
            "**G6 Counterfactual twins:** move one object; 484 of 484 answers changed",
            "**G7 Deterministic:** same seed, byte-identical scene files and answers",
            "**G8 Real photos** registered to their layout: 36 of 36 correct views pass, 3 of 3 "
            "misplaced rejected",
        ]  # fmt: skip
        self.text(
            s, 4.95, 1.55, R - 4.95, 3.8, gates, size=10.5, bullets=True, space=4.5, line=1.02
        )

    def variation(self) -> None:
        s = self.page("Features: meaningful variation", "Variation in what is tested, not the banner colour",
                      "The variation changes what the model must do: which skill, which camera, "
                      "how cluttered, how hidden, how bad the camera. And the dials move the "
                      "models' accuracy, which is the proof that they matter.")  # fmt: skip
        self.picture(s, PACK / "hero_triptych.png", L, 1.52, 5.55, 1.56)
        self.picture(s, PACK / "camera_dial_strip.png", L, 3.2, 5.55, 1.22)
        self.text(s, L, 4.5, 5.55, 0.85, "One v1 scene from the three robot cameras (top) and its front "
                  "view through the four camera-quality presets (bottom).", size=9.5, color=GREY,
                  space=0)  # fmt: skip
        x = 6.2
        self.text(s, x, 1.52, R - x, 3.85, [
            "**12 question families:** count, count by colour, nearest to gripper, front to back, "
            "relation, occlusion, support, containment, reachability, free space, distance, gripper view",
            "**3 robot cameras:** overhead, front, gripper",
            "**Difficulty dials:** 2 to 25 objects, stacks, containers, look-alikes, occlusion, "
            "4 lighting setups, camera quality from clean to CCTV; 44 everyday objects",
            "**The dials move the models:** evidence under 60% visible costs {{HIDDEN_COST}} points; "
            "a harsh camera costs Sonnet and Opus {{HARSH_COST_SONNET_OPUS}}; each 10 more objects "
            "cuts the odds of a right answer to {{OBJECTS_OR_RANGE}}",
        ], size=10.5, bullets=True, space=6, line=1.03)  # fmt: skip

    def headline(self) -> None:
        s = self.page("Evaluation of frontier models: the headline",
                      "Claude models we tested drop ~{{GAP_ABOUT}} points when the 2D picture misleads",
                      "Same {{TASKS_PER_MODEL}} tasks for every model. Where the flat 2D reading "
                      "agrees with the 3D truth, accuracy is 55 to 74%; where it conflicts, 30 to "
                      "48%. Thinking harder does not close it.")  # fmt: skip
        self.picture(s, MEDIA / "shortcut_conflict_accuracy.png", L, 1.5, 5.75, 3.25)
        self.text(s, L, 4.8, 5.75, 0.55,
                  "The Claude models we tested, on the same {{TASKS_PER_MODEL}} tasks: "
                  "{{SPATIAL_TASKS}} spatial, {{CONFLICT_TASKS}} where the 2D shortcut conflicts with "
                  "the 3D truth. 95% intervals from a bootstrap over scenes.",
                  size=9, color=GREY, space=0)  # fmt: skip
        x, w = 6.35, R - 6.35
        self.card(
            s,
            x,
            1.52,
            w,
            1.18,
            "More thinking doesn't help",
            "Opus xhigh vs high changes the gap by {{XHIGH_GAP_CHANGE}}.",
            size=10.5,
        )
        self.card(
            s,
            x,
            2.82,
            w,
            1.18,
            "The picture pulls the answer",
            "On nearest to gripper, {{NEAREST_CAPTURE}}.",
            accent=ORANGE,
            size=10.5,
        )
        self.card(
            s,
            x,
            4.12,
            w,
            1.2,
            "Not just a harder subset",
            "Same family and camera: {{MATCHED_RANGE}} points lower. Occlusion, margin, "
            "clutter, camera held fixed: odds ratio {{CONFLICT_OR_RANGE}}.",
            size=10,
        )

    def supporting(self) -> None:
        s = self.page("Evaluation: what else breaks", "They don't track the scene, and the dials find where they break",
                      "Counterfactual twins: same question, one object moved, a new truth. Breaking "
                      "points: the factory's difficulty dials. Scale stated: give the grid scale "
                      "and distances improve.")  # fmt: skip
        y, h = 1.52, 1.95
        half = (W - 0.15) / 2
        for i, img in enumerate((MEDIA / "twin_flip.png", MEDIA / "breaking_point.png")):
            x = L + i * (half + 0.15)
            self.rect(s, x, y, half, h, fill=WHITE, line=LINE, radius=0.04)
            self.picture(s, img, x + 0.06, y + 0.06, half - 0.12, h - 0.12)
        cw, gap, y2, h2 = 2.97, 0.145, 3.6, 1.72
        cards = [
            ("Counterfactual twins", "Move one object and ask again: both versions right only "
             "{{TWIN_BOTH_RIGHT}} of the time; on {{TWIN_STICKS}} of {{TWIN_PAIRS}} pairs they repeat "
             "the old answer.", BLUE),
            ("Breaking points", "Evidence under 60% visible: −{{HIDDEN_COST}} points. Harsh camera: "
             "−{{HARSH_COST_SONNET_OPUS}} (Sonnet, Opus). Each 10 more objects: odds {{OBJECTS_OR_RANGE}}.",
             BLUE),
            ("Scale stated", "State the printed grid's scale and distance answers improve: ^^Sonnet "
             "+10 points^^, Haiku +5, Opus +1, on 272 re-asked tasks. Programs 0.3.0 now state it.",
             ORANGE),
        ]  # fmt: skip
        for i, (head, body, accent) in enumerate(cards):
            self.card(s, L + i * (cw + gap), y2, cw, h2, head, body, accent=accent, size=10.5)

    def physical_twins(self) -> None:
        s = self.page("Physical twins: real photos, exact answers", "It's real: the same trap on a real table",
                      "Real objects arranged on a printed mat exactly as a generated layout says. "
                      "G8 registers each photo to its layout, so the photo carries the layout's "
                      "exact answer. {{REAL_TRAP}} {{SIM2REAL}}")  # fmt: skip
        iw, ih, y = 3.05, 2.29, 1.52
        self.picture(s, ROOT / "docs/twins/media/H1_hero_0297_front.jpg", L, y, iw, ih)
        self.text(
            s,
            L,
            y + ih + 0.03,
            iw,
            0.26,
            "Render (digital twin of layout H1)",
            size=9,
            color=GREY,
            space=0,
        )
        x2 = L + iw + 0.14
        if self.h1_photo and self.h1_photo.is_file():
            self.picture(s, self.h1_photo, x2, y, iw, ih)
        else:
            ph = self.rect(
                s, x2, y, iw, ih, fill=RGBColor(0xFB, 0xF1, 0xE8), line=ORANGE, name="PHOTO_H1"
            )
            ph.line.dash_style = 4
            self.text(s, x2 + 0.1, y + 0.8, iw - 0.2, 0.7, ["Real photo of layout H1",
                      ("goes here in Phase 2", 10)], size=12, color=ORANGE, font=HEAD,
                      align=PP_ALIGN.CENTER, space=2)  # fmt: skip
        self.text(
            s,
            x2,
            y + ih + 0.03,
            iw,
            0.26,
            "Real photo, same camera pose",
            size=9,
            color=GREY,
            space=0,
        )
        x3 = x2 + iw + 0.2
        self.text(s, x3, y - 0.02, R - x3, 2.6, [
            ("“Is there room to place the tape on the table between the bottle and the lime?”", 11.5),
            ("**Truth: yes.** Gap 6.9 cm, tape 4.5 cm; check it on the printed grid.", 10.5),
            ("On the render, Sonnet and Opus both say ~~no~~: the tape is nearer the lens, so it looks "
             "wider than the gap.", 10.5),
        ], space=6, line=1.04)  # fmt: skip
        self.card(
            s, L, 4.2, 6.24, 1.13, "On the real photo", "{{REAL_TRAP}}", accent=ORANGE, size=10
        )
        self.card(s, 6.78, 4.2, R - 6.78, 1.13, "Sim to real", "{{SIM2REAL_SHORT}}. {{SIM2REAL_ENDING}}",
                  size=10)  # fmt: skip
        self.text(s, x3, 3.45, R - x3, 0.7, "Printed mat with a cm grid and ArUco markers; G8 rejects a "
                  "photo whose objects are off their layout.", size=9, color=GREY, space=0)  # fmt: skip

    def human_baseline(self) -> None:
        s = self.page(
            "Validation: a human check", "Human baseline: the founder's blind audit", "{{AUDIT}}"
        )
        self.rect(s, L, 1.6, 4.1, 2.3, fill=LIGHT)
        self.text(s, L + 0.2, 1.72, 3.7, 2.1, ["{{AUDIT_SHORT}}",
                  ("on the same audited tasks", 11)], size=24, font=HEAD, color=BLUE,
                  anchor=MSO_ANCHOR.MIDDLE, align=PP_ALIGN.CENTER, space=6)  # fmt: skip
        x = 4.75
        self.text(s, x, 1.6, R - x, 1.5, "{{AUDIT}}", size=13, line=1.08, space=0)
        self.text(s, x, 3.15, R - x, 2.2, [
            "150 tasks stratified by family, camera and shortcut conflict; 46 where the 2D shortcut misleads",
            "Answered blind: no truth and no model answers on the page",
            "Scored by the same typed checker as the models; distance questions state the grid scale for both",
            "Every disagreement labelled: our bug, ambiguous, or human error",
        ], size=10.5, bullets=True, color=GREY, space=4)  # fmt: skip
        self.text(s, L, 4.05, 4.1, 1.2, "Why it matters: if a person reading the same images gets them "
                  "right, the models' misses are model failures, not broken tasks.", size=10, color=GREY,
                  space=0)  # fmt: skip

    def repeatability(self) -> None:
        s = self.page("Working, repeatable implementation", "One command gives a judge the same tasks",
                      "Fresh clone, uv sync, vtf quickstart: about four minutes to 112 verified "
                      "tasks. vtf verify re-renders and compares: truth exact, images near-exact.")  # fmt: skip
        self.rect(s, L, 1.55, 4.75, 2.45, fill=TERM_BG, radius=0.04)
        self.text(s, L + 0.15, 1.7, 4.5, 2.25, [
            "$ git clone https://github.com/",
            "    Zugzwang-world/hrishikesh-visual-task-factory",
            "$ uv sync",
            "$ uv run vtf quickstart",
            "  7 scenes, 21 images, 112 tasks, 12 families",
            "$ uv run vtf verify out/quick --sample 2",
            "  truth: exact   images: SSIM ≥ 0.998",
        ], size=10.5, color=RGBColor(0xE6, 0xE9, 0xEF), font=MONO, space=3, line=1.0)  # fmt: skip
        self.text(s, L, 4.1, 4.75, 1.1, ["Needs uv and Blender 5.3; no API key to generate tasks.",
                  "Judges who skip rendering can download the pre-rendered v1 from Hugging Face and "
                  "re-check any task's answer from its scene file."], size=10, color=GREY, space=4)  # fmt: skip
        x = 5.4
        self.text(s, x, 1.52, R - x, 3.85, [
            "**Fresh clone to verified tasks in 4 min 15 s**, checked from a clean checkout",
            "**Byte-identical across checkouts:** scene files, masks, depth, task IDs and answers",
            "**vtf verify** re-samples and re-renders: truth exact, images near-exact "
            "(GPU re-render SSIM 0.998 to 0.9999)",
            "It **caught a real renderer material bug** in v1; we disclose it (answers, masks "
            "and depth unaffected)",
            "Every generator seeded; a task ID is a content hash",
            "**Dataset v1 on Hugging Face:** 3,181 tasks, 29,381 files, every gate's evidence and "
            "every model answer, CC0",
        ], size=10.5, bullets=True, space=5, line=1.03)  # fmt: skip

    def impact(self) -> None:
        s = self.page("Impact for Innodata, cost and links", "Find the failure before the robot does",
                      "Innodata sells test and evaluation, training data and RL environments, and "
                      "calls physical AI its next market. This factory serves all three.")  # fmt: skip
        cols = [
            ("Test and evaluation", "Risk cards per model in T&E terms: risk, severity, triggers, "
             "error types, mitigation. A new skill is a recipe and a program."),
            ("Training data", "Unlimited fresh scenes with exact answers, three cameras and "
             "difficulty dials; never seen in any model's training data."),
            ("Verifiable rewards", "Every answer is a program on the scene file: a reward for robot "
             "learning with no AI judge."),
        ]  # fmt: skip
        cw, gap = 2.97, 0.145
        for i, (head, body) in enumerate(cols):
            self.card(s, L + i * (cw + gap), 1.55, cw, 1.42, head, body, size=10.5,
                      accent=ORANGE if i == 2 else BLUE)  # fmt: skip
        self.text(s, L, 3.1, W, 0.75, [
            "**Cost:** every model call so far, about $46 (Message Batches, half price). Generating "
            "tasks needs no API key: a fresh clone reaches 112 verified tasks in about four minutes "
            "on a laptop.",
            "**Ready to extend:** another model provider is one adapter in the same harness; a new "
            "question family is a recipe and a program.",
        ], size=10.5, space=5, line=1.04)  # fmt: skip
        self.rect(s, L, 4.02, W, 1.12, fill=RGBColor(0xEA, 0xF2, 0xFB))
        self.text(s, L + 0.18, 4.12, W - 0.3, 1.0, [
            f"**Code:** {REPO}",
            f"**Task Explorer:** {EXPLORER}   (conflict gallery: add ?view=gallery)",
            f"**Dataset:** {DATASET}",
            "**Findings and risk cards:** docs/reports/v1/findings.md and risk_cards.md in the repo",
        ], size=10.5, space=3, line=1.0)  # fmt: skip


_BLOB_CACHE: dict[int, str] = {}


def _blob_path(blob: bytes) -> str:
    """add_picture wants a file; python-pptx dedups identical images, so the deck holds one copy."""
    key = hash(blob)
    if key not in _BLOB_CACHE:
        import tempfile

        f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        f.write(blob)
        f.close()
        _BLOB_CACHE[key] = f.name
    return _BLOB_CACHE[key]


def build(
    n: dict[str, str | None] | None = None, out: Path = OUT, h1_photo: Path | None = None
) -> Path:
    n = numbers() if n is None else n
    if h1_photo is None and n.get("H1_PHOTO"):
        h1_photo = Path(n["H1_PHOTO"])
    prs = Deck(n, h1_photo).build()
    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out))
    left = sorted({t for s in prs.slides for sh in s.shapes if sh.has_text_frame
                   for t in re.findall(r"\{\{[A-Z0-9_]+\}\}", sh.text_frame.text)})  # fmt: skip
    if not (h1_photo and h1_photo.is_file()):
        left.append("H1 photo")
    print(f"{out}: {len(prs.slides)} slides; placeholders left: {', '.join(left) or 'none'}")
    return out


if __name__ == "__main__":
    build()
