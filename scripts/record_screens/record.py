# ruff: noqa: E501 -- the embedded JS and CSS and the ffmpeg filter strings read better unwrapped
"""Record the demo-video screen segments and the media pack (brief VTF-09).

Regenerate everything with one command (about 6 minutes; the Explorer segments need network):

    uv run --with playwright==1.64.0 python scripts/record_screens/record.py

    --only seg01,seg04,pack   a subset (seg01..seg07, pack)
    --capture                 first re-run `uv run vtf quickstart` and then
                              `uv run vtf verify out/quick --sample 2` in this checkout, under
                              scripts/blender_lock.sh, and capture their real output with timing
                              (pty_cast.py) into out/video/*_cast.json. Without it the casts
                              already there are replayed. Delete out/quick first for a fresh run.

playwright 1.64.0 drives Chromium build 1248 (already in ~/Library/Caches/ms-playwright), headless
with GPU WebGL (ANGLE on Metal). Every segment is recorded frame by frame: the page is put in the
state of frame k (a scroll position, a mouse position, a time t on the page's own timeline),
captured as PNG at exactly 1920 x 1080 (CDP Page.captureScreenshot, clip scaled by the device
pixel ratio), and the PNGs are piped straight into ffmpeg (libx264, CRF 23, yuv420p, BT.709,
+faststart, 30 fps, no audio), so no frame files are written. For the live Explorer's 3D view
Playwright's fake clock is installed and paused, and each frame advances it by exactly 1/30 s,
so three.js renders (and OrbitControls damps) on a fixed timeline however long a capture takes.
Playwright's recordVideo was tried first and dropped: it records CSS pixels only (1440 x 810 in
the corner of a grey 1920 x 1080 frame; deviceScaleFactor is ignored), at a fixed 25 fps, as
soft VP8.

Lengths follow docs/video/edit.yaml: seg01 12 s, seg02 9 s, seg03 11 s, seg04 10 s, seg05 12 s,
seg06 12 s, seg07 9 s. The terminal replays type the command in 0.65 s and take the smallest
whole speed-up that leaves the command's last output on screen for the final 3 s; the badge
shows that factor and a clock shows the real elapsed time.

Screenshots do not show the system cursor, so the Explorer segments draw one (an arrow, with a
ring while the button is down) that follows the real mouse events Playwright sends.

Outputs: docs/video/segments/seg01..seg07_*.mp4; docs/media/pack/turntable_1.mp4,
turntable_2.mp4, turntable_1.gif, gallery_overview.png, gallery_trap_open.png,
twin_before_after.png. Pages it records: docs/video/pages/{chart,twin,terminal}.html.
"""

import argparse
import asyncio
import base64
import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from playwright.async_api import Page, async_playwright

ROOT = Path(__file__).resolve().parents[2]
SEG = ROOT / "docs" / "video" / "segments"
PAGES = ROOT / "docs" / "video" / "pages"
PACK = ROOT / "docs" / "media" / "pack"
OUTV = ROOT / "out" / "video"
EXPLORER = "https://vtf-task-explorer.vercel.app"
FPS = 30
W, H = 1920, 1080
# Explorer pages: a 1440 x 810 CSS viewport at 4/3 device pixels gives 1920 x 1080 screenshots
# with text a third larger than a 1920-wide layout would have.
EXPLORER_VIEW = {"viewport": {"width": 1440, "height": 810}, "device_scale_factor": 4 / 3}
PAGE_VIEW = {"viewport": {"width": W, "height": H}, "device_scale_factor": 1}
LAUNCH_ARGS = ["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"]

# The trap task (the slides use the same scene): scene_0103, front camera, task 4 of 11,
# t08138724d906efd6 "Which object is nearest to the gripper's tip?", truth jug, 2D shortcut box.
TRAP_SCENE, TRAP_TASK = "scene_0103", 4
# A cluttered second turntable: scene_0194 (24 objects), task 4 (count, one evidence object).
CLUTTER_SCENE, CLUTTER_TASK = "scene_0194", 4

ENCODE = [
    "-an", "-c:v", "libx264", "-preset", "slow", "-crf", "23",
    "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
    "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
    "-movflags", "+faststart",
]  # fmt: skip

CURSOR_JS = """
(() => {
  if (window.__vtfCursor) return;
  const arrow = document.createElement('div');
  arrow.innerHTML = '<svg width="30" height="30" viewBox="0 0 30 30"><path d="M5 3 L5 24 L10.6 18.6 L14.4 27.2 L18.2 25.6 L14.5 17.2 L22 17.2 Z" fill="#111" stroke="#fff" stroke-width="1.8" stroke-linejoin="round"/></svg>';
  const ring = document.createElement('div');
  arrow.id = '__vtf_cursor'; ring.id = '__vtf_ring';
  const base = {position: 'fixed', left: '0px', top: '0px', pointerEvents: 'none', zIndex: '2147483647', transform: 'translate(-200px,-200px)'};
  Object.assign(arrow.style, base, {width: '30px', height: '30px'});
  Object.assign(ring.style, base, {zIndex: '2147483646', width: '40px', height: '40px', marginLeft: '-20px', marginTop: '-20px', borderRadius: '50%', background: 'rgba(42,120,214,.22)', border: '2px solid rgba(42,120,214,.85)', opacity: '0'});
  document.documentElement.append(ring, arrow);
  const pos = (e) => {
    arrow.style.transform = `translate(${e.clientX - 5}px, ${e.clientY - 3}px)`;
    ring.style.transform = `translate(${e.clientX}px, ${e.clientY}px)`;
  };
  for (const t of ['mousemove', 'pointermove']) addEventListener(t, pos, true);
  addEventListener('pointerdown', (e) => { pos(e); ring.style.opacity = '1'; }, true);
  addEventListener('pointerup', () => { ring.style.opacity = '0'; }, true);
  window.__vtfCursor = true;
})();
"""

# Fills the viewport with the Explorer's 3D pane (turntables): CSS only, the app is untouched.
FULL3D_CSS = """
.pane-3d { position: fixed !important; inset: 0 !important; width: 100vw !important; height: 100vh !important;
           aspect-ratio: auto !important; z-index: 100000 !important; border-radius: 0 !important; margin: 0 !important;
           background: linear-gradient(180deg, #f6f4f0 0%, #e9e5dd 100%) !important; border: 0 !important; }
.pane-3d .pane-tag, .pane-3d .pane-btn { display: none !important; }
"""


def ease(x: float) -> float:
    """Smoothstep, clamped to [0, 1]."""
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def ease_cos(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 0.5 - 0.5 * math.cos(math.pi * x)


class Encoder:
    """PNG frames on stdin -> H.264 MP4."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "image2pipe", "-framerate", str(FPS),
               "-c:v", "png", "-i", "-", *ENCODE, "-r", str(FPS), str(path)]  # fmt: skip
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        self.n = 0
        self.last: bytes | None = None

    def write(self, png: bytes, times: int = 1) -> None:
        for _ in range(times):
            self.proc.stdin.write(png)
            self.n += 1
        self.last = png

    def close(self) -> None:
        self.proc.stdin.close()
        if self.proc.wait() != 0:
            raise SystemExit(f"ffmpeg failed for {self.path}")
        print(f"  {self.path.relative_to(ROOT)}: {self.n} frames, {self.n / FPS:.1f} s")


class Shot:
    """One recorded segment: a page, an encoder and the frame clock.

    Frames are captured with CDP Page.captureScreenshot (clip scaled by devicePixelRatio, so
    1920 x 1080 device pixels): Playwright's page.screenshot waits on the page's own
    requestAnimationFrame, which never fires while the fake clock is paused."""

    def __init__(self, page: Page, enc: Encoder, cdp, clip: dict):
        self.page, self.enc, self.cdp, self.clip = page, enc, cdp, clip
        self.clock = False
        self._acc = 0.0
        self.mouse = (W / 2, H / 2)

    @classmethod
    async def create(cls, page: Page, out: Path | None) -> "Shot":
        """out=None: stills only, no video."""
        cdp = await page.context.new_cdp_session(page)
        vp = page.viewport_size
        dpr = await page.evaluate("devicePixelRatio")
        clip = {"x": 0, "y": 0, "width": vp["width"], "height": vp["height"], "scale": dpr}
        return cls(page, Encoder(out) if out else None, cdp, clip)

    async def capture(self) -> bytes:
        # The clip is in document coordinates: follow the scroll position.
        sx, sy = await self.page.evaluate("[scrollX, scrollY]")
        clip = dict(self.clip, x=sx, y=sy)
        r = await self.cdp.send(
            "Page.captureScreenshot", {"format": "png", "fromSurface": True, "clip": clip}
        )
        return base64.b64decode(r["data"])

    async def still(self, path: Path) -> None:
        path.write_bytes(await self.capture())

    async def install_clock(self) -> None:
        """Fake, paused clock: from here on the page's timers and rAF run only on tick()."""
        await self.page.clock.install()
        now = await self.page.evaluate("Date.now()")
        await self.page.clock.pause_at(now + 50)
        self.clock = True
        await self.page.clock.run_for(100)

    async def tick(self) -> None:
        if self.clock:
            self._acc += 1000 / FPS
            ms = int(self._acc)
            self._acc -= ms
            await self.page.clock.run_for(ms)
        else:
            await self.page.evaluate(
                "new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
            )

    async def frame(self) -> bytes:
        await self.tick()
        png = await self.capture()
        self.enc.write(png)
        return png

    async def hold(self, seconds: float, live: bool = True) -> None:
        n = round(seconds * FPS)
        if not live and n:
            png = await self.frame()
            self.enc.write(png, n - 1)
            return
        for _ in range(n):
            await self.frame()

    async def scroll_to(self, y: float, seconds: float) -> None:
        y0 = await self.page.evaluate("scrollY")
        n = max(1, round(seconds * FPS))
        for k in range(1, n + 1):
            await self.page.evaluate(f"scrollTo(0, {y0 + (y - y0) * ease_cos(k / n):.2f})")
            await self.frame()

    async def move(self, x: float, y: float, seconds: float) -> None:
        x0, y0 = self.mouse
        n = max(1, round(seconds * FPS))
        for k in range(1, n + 1):
            e = ease_cos(k / n)
            await self.page.mouse.move(x0 + (x - x0) * e, y0 + (y - y0) * e)
            await self.frame()
        self.mouse = (x, y)

    async def place(self, x: float, y: float) -> None:
        await self.page.mouse.move(x, y)
        self.mouse = (x, y)


async def settle(page: Page, ms: int = 1500) -> None:
    await page.wait_for_load_state("networkidle")
    # Images that have started loading (lazy ones below the fold never do), at most 8 s.
    await page.evaluate(
        """Promise.race([new Promise(r => setTimeout(r, 8000)), Promise.all([...document.images]
             .filter(i => !i.complete && (i.loading !== 'lazy' || i.getBoundingClientRect().top < innerHeight))
             .map(i => new Promise(r => { i.addEventListener('load', r); i.addEventListener('error', r); })))])"""
    )
    await page.wait_for_timeout(ms)


async def open_page(browser, view: dict, url: str, wait_ms: int = 1500) -> tuple:
    ctx = await browser.new_context(**view)
    page = await ctx.new_page()
    await page.goto(url, wait_until="networkidle")
    await settle(page, wait_ms)
    return ctx, page


async def doc_y(page: Page, selector: str) -> float:
    return await page.evaluate(
        f"document.querySelector({json.dumps(selector)}).getBoundingClientRect().top + scrollY"
    )


# ---------------------------------------------------------------- Explorer segments


async def seg01(browser) -> None:
    """12 s: gallery -> the scene_0103 'nearest to gripper' card -> image marks -> 3D orbit."""
    ctx, page = await open_page(browser, EXPLORER_VIEW, f"{EXPLORER}/?view=gallery")
    await page.evaluate(f"scrollTo(0, {await doc_y(page, '.gallery') - 14})")
    await page.evaluate(CURSOR_JS)
    await page.wait_for_timeout(500)
    shot = await Shot.create(page, SEG / "seg01_trap_rotate.mp4")
    await shot.place(1250, 40)
    await shot.hold(0.5)
    card = page.locator(f'button.g-open[aria-label*="{TRAP_SCENE},"]')
    box = await card.bounding_box()
    await shot.move(box["x"] + box["width"] * 0.55, box["y"] + box["height"] * 0.42, 0.75)
    await shot.hold(0.25)
    await page.mouse.down()
    await shot.hold(0.1)
    await page.mouse.up()
    # The scene loads (scene.json, images, glTF) in real time between these two frames.
    await page.wait_for_selector(".pane-3d canvas")
    await settle(page, 3500)
    assert f"scene={TRAP_SCENE}" in page.url and f"task={TRAP_TASK}" in page.url, page.url
    await shot.install_clock()
    await shot.scroll_to(await doc_y(page, ".toolbar") - 12, 0.8)
    img = await page.locator(".views .pane").first.bounding_box()
    await shot.move(img["x"] + img["width"] * 0.50, img["y"] + img["height"] * 0.68, 0.6)
    await shot.hold(0.9)
    canvas = await page.locator(".pane-3d canvas").bounding_box()
    sx, sy = canvas["x"] + canvas["width"] * 0.30, canvas["y"] + canvas["height"] * 0.70
    await shot.move(sx, sy, 0.5)
    await page.mouse.down()
    await shot.hold(0.1)
    # OrbitControls turns 2*pi per element height of horizontal drag: 100 degrees here.
    dx = canvas["height"] * 100 / 360
    await shot.move(sx + dx, sy, 5.2)
    await page.mouse.up()
    # glide the cursor off the 3D view so the end frame is clear
    await shot.move(canvas["x"] + canvas["width"] * 0.55, canvas["y"] + canvas["height"] + 70, 0.6)
    await shot.hold(1.7)
    shot.enc.close()
    # The opened trap card as a still for the media pack (image with marks plus the 3D view).
    await page.evaluate(
        "for (const id of ['__vtf_cursor', '__vtf_ring']) document.getElementById(id).style.display = 'none'"
    )
    await shot.tick()
    await shot.still(PACK / "gallery_trap_open.png")
    await ctx.close()


async def seg02(browser) -> None:
    """9 s: the 'How it's verified' panel, readable from the first frame. A 1024 x 576 CSS
    viewport (at 1.875 device pixels) lays the gates out 3 by 2 and fills the frame with them."""
    vw, vh = 1024, 576
    view = {"viewport": {"width": vw, "height": vh}, "device_scale_factor": W / vw}
    ctx, page = await open_page(browser, view, f"{EXPLORER}/")
    top = await doc_y(page, "details.verify")
    height = await page.evaluate("document.querySelector('details.verify').getBoundingClientRect().height")
    shot = await Shot.create(page, SEG / "seg02_verified_panel.mp4")
    if height + 30 <= vh:  # fits: centre it and drift 20 px over the shot
        y = max(10.0, top - (vh - height) / 2)
        await page.evaluate(f"scrollTo(0, {y + 10})")
        await shot.scroll_to(y - 10, 9.0)
    else:  # does not fit: start at its top, scroll slowly to its bottom
        await page.evaluate(f"scrollTo(0, {top - 14})")
        await shot.hold(2.5, live=False)
        await shot.scroll_to(top + height - vh + 14, 4.5)
        await shot.hold(2.0, live=False)
    shot.enc.close()
    await ctx.close()


async def pack_gallery(browser) -> None:
    ctx, page = await open_page(browser, EXPLORER_VIEW, f"{EXPLORER}/?view=gallery", 2500)
    await page.evaluate(f"scrollTo(0, {await doc_y(page, '.gallery') - 14})")
    await page.wait_for_timeout(800)
    shot = await Shot.create(page, None)
    await shot.still(PACK / "gallery_overview.png")
    await ctx.close()


async def turntable(browser, scene: str, task: int, out: Path, labels: str, seconds: float = 8.0,
                    tilt: float = 55.0, zoom_out: int = 1, gif: Path | None = None) -> None:  # fmt: skip
    """A seamless full orbit of the Explorer's 3D view, the pane filling the frame. It starts
    from the overhead camera (whose view centre is near the table centre, so the orbit stays
    centred), tilts down `tilt` degrees, then turns 360 degrees at constant speed."""
    ctx, page = await open_page(browser, EXPLORER_VIEW, f"{EXPLORER}/?scene={scene}&task={task}", 3500)
    await page.locator(".toolbar .seg button", has_text="Overhead").click()
    await page.wait_for_timeout(800)
    await page.add_style_tag(content=FULL3D_CSS)
    if labels != "evidence":
        await page.select_option(".view-opts select", labels)
    await page.wait_for_timeout(1500)
    shot = await Shot.create(page, out)
    await shot.install_clock()
    for _ in range(5):
        await shot.tick()
    h = await page.evaluate("document.querySelector('.pane-3d canvas').parentElement.clientHeight")
    n = round(seconds * FPS)
    step = h / n  # OrbitControls: one element height of horizontal drag = one full turn
    x, y = 270.0, 560.0
    await page.mouse.move(x, y)
    for _ in range(zoom_out):  # dolly out a notch so the whole table stays in frame
        await page.mouse.wheel(0, 100)
        await shot.tick()
    await page.mouse.down()
    for _ in range(20):  # tilt down from the overhead view (not recorded)
        y -= tilt / 360 * h / 20
        await page.mouse.move(x, y)
        await shot.tick()
    for _ in range(40):  # reach a steady orbit speed before the first recorded frame
        x += step
        await page.mouse.move(x, y)
        await shot.tick()
    await shot.capture()  # warm-up: the first capture after a long unrecorded run can be blank
    for _ in range(n):
        x += step
        await page.mouse.move(x, y)
        await shot.frame()
    await page.mouse.up()
    shot.enc.close()
    await ctx.close()
    if gif:
        make_gif(out, gif)


def make_gif(mp4: Path, gif: Path, width: int = 960, limit_mb: float = 4.0) -> None:
    """A looping GIF for the README, under `limit_mb`. A turntable changes almost every pixel
    every frame, so the GIF keeps every 5th frame of the 30 fps video and plays it at 12 fps
    (the same full orbit in 4 s instead of 8), with an undithered palette: the first of these
    (keep every, fps, colours) settings that fits wins."""
    tries = [(5, 12, 96), (5, 12, 64), (6, 10, 64), (6, 10, 48)]
    OUTV.mkdir(parents=True, exist_ok=True)
    pal = OUTV / "record_screens_palette.png"
    for every, fps, colors in tries:
        vf = f"select='not(mod(n\\,{every}))',setpts=N/{fps}/TB,scale={width}:-1:flags=lanczos"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(mp4), "-vf",
                        f"{vf},palettegen=max_colors={colors}:stats_mode=full", str(pal)], check=True)  # fmt: skip
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(mp4), "-i", str(pal), "-lavfi",
                        f"{vf}[x];[x][1:v]paletteuse=dither=none:diff_mode=rectangle",
                        "-r", str(fps), "-loop", "0", str(gif)], check=True)  # fmt: skip
        mb = gif.stat().st_size / 1e6
        print(f"  {gif.relative_to(ROOT)}: {mb:.2f} MB (every {every}th frame at {fps} fps, {colors} colours)")
        if mb <= limit_mb:
            break
    pal.unlink()
    if mb > limit_mb:
        raise SystemExit(f"{gif} is over {limit_mb} MB")


# ---------------------------------------------------------------- local pages


async def page_timeline(browser, url: str, out: Path, seconds: float, still: Path | None = None) -> None:
    """A local page with window.setT(t, duration): one capture per frame at t = k / FPS."""
    ctx, page = await open_page(browser, PAGE_VIEW, url, 500)
    shot = await Shot.create(page, out)
    n = round(seconds * FPS)
    for k in range(n):
        await page.evaluate(f"setT({k / FPS:.5f}, {seconds})")
        await shot.frame()
    shot.enc.close()
    if still:
        await shot.still(still)
    await ctx.close()


async def seg06(browser) -> None:
    """About 12 s: the v1 dataset report, a slow scroll through the gate funnel, rejections and
    coverage, pausing at the top of each."""
    ctx, page = await open_page(browser, EXPLORER_VIEW, (ROOT / "docs/reports/v1/index.html").as_uri(), 800)
    shot = await Shot.create(page, SEG / "seg06_report_scroll.mp4")
    gates = await doc_y(page, "#gates") - 20
    rejections = await doc_y(page, "#rejections") - 20
    coverage = await doc_y(page, "#coverage") - 20
    end = await doc_y(page, "#answers") - 810 + 10  # the coverage section ends where answers begin
    await page.evaluate(f"scrollTo(0, {gates})")
    await shot.hold(1.3, live=False)
    await shot.scroll_to(rejections, 2.2)
    await shot.hold(0.8, live=False)
    await shot.scroll_to(coverage, 2.6)
    await shot.hold(0.8, live=False)
    await shot.scroll_to(end, 3.2)
    await shot.hold(1.1, live=False)
    shot.enc.close()
    await ctx.close()


TYPE_START, TYPE_END = 0.15, 0.8  # the command is typed in 0.65 s


async def terminal(browser, cast_path: Path, out: Path, seconds: float, title: str,
                   tail: float = 3.0, rows: int | None = None) -> None:  # fmt: skip
    """Replay a real capture, sped up by the smallest whole factor that lands the command's
    last output at least `tail` seconds before the end, so its summary stays on screen."""
    cast = json.loads(cast_path.read_text())
    last = cast["events"][-1][0] if cast["events"] else cast["duration"]
    end_at = max(last, cast["duration"])
    speed = max(1, math.ceil(end_at / (seconds - tail - TYPE_END)))
    ctx, page = await open_page(browser, PAGE_VIEW, (PAGES / "terminal.html").as_uri(), 300)
    opts = {"speed": speed, "title": title, "rows": rows, "typeStart": TYPE_START, "typeEnd": TYPE_END}
    await page.evaluate("([c, o]) => vtfTerm.init(c, o)", [cast, opts])
    shot = await Shot.create(page, out)
    n = round(seconds * FPS)
    for k in range(n):
        await page.evaluate(f"vtfTerm.seek({k / FPS:.5f})")
        await shot.frame()
    done = TYPE_END + end_at / speed
    print(f"  replay: {cast['duration']:.1f} s of real output at x{speed}; "
          f"last output at {done:.1f} s, on screen for the last {seconds - done:.1f} s")  # fmt: skip
    shot.enc.close()
    await ctx.close()


# ---------------------------------------------------------------- capture


def capture() -> None:
    """Re-run quickstart and verify under the shared Blender lock, capturing their output."""
    OUTV.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["ANTHROPIC_API_KEY"] = ""  # no model calls: quickstart then runs only fake-oracle
    # The lock lives in the main checkout's out/logs, which every worktree shares.
    common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    env.setdefault("VTF_LOGS", str(Path(common).parent / "out" / "logs"))
    py = [sys.executable, str(ROOT / "scripts/record_screens/pty_cast.py")]
    inner = (
        f"{' '.join(py)} --out out/video/quickstart_cast.json --show 'uv run vtf quickstart' "
        f"-- uv run vtf quickstart && "
        f"{' '.join(py)} --out out/video/verify_cast.json "
        f"--show 'uv run vtf verify out/quick --sample 2' -- uv run vtf verify out/quick --sample 2"
    )
    cmd = [str(ROOT / "scripts/blender_lock.sh"), "vtf09-video-capture", "--", "bash", "-c", inner]
    subprocess.run(cmd, cwd=ROOT, env=env, check=True)


# ---------------------------------------------------------------- main


ALL = ["seg01", "seg02", "seg03", "seg04", "seg05", "seg06", "seg07", "pack"]


async def run(only: list[str]) -> None:
    SEG.mkdir(parents=True, exist_ok=True)
    PACK.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=LAUNCH_ARGS)
        t0 = time.time()
        for name in only:
            print(f"{name} ...")
            if name == "seg01":
                await seg01(browser)
            elif name == "seg02":
                await seg02(browser)
            elif name == "seg03":
                await page_timeline(browser, (PAGES / "chart.html").as_uri(),
                                    SEG / "seg03_headline_chart.mp4", 11.0)  # fmt: skip
            elif name == "seg04":
                await page_timeline(browser, (PAGES / "twin.html").as_uri(),
                                    SEG / "seg04_twin_before_after.mp4", 10.0,
                                    still=PACK / "twin_before_after.png")  # fmt: skip
            elif name == "seg05":
                await terminal(browser, OUTV / "quickstart_cast.json",
                               SEG / "seg05_quickstart_terminal.mp4", 12.0,
                               "uv run vtf quickstart  ·  fresh checkout")  # fmt: skip
            elif name == "seg06":
                await seg06(browser)
            elif name == "seg07":
                await terminal(browser, OUTV / "verify_cast.json",
                               SEG / "seg07_verify_terminal.mp4", 9.0,
                               "uv run vtf verify out/quick --sample 2", rows=14)  # fmt: skip
            elif name == "pack":
                await pack_gallery(browser)
                await turntable(browser, TRAP_SCENE, TRAP_TASK, PACK / "turntable_1.mp4",
                                "evidence", gif=PACK / "turntable_1.gif")  # fmt: skip
                await turntable(browser, CLUTTER_SCENE, CLUTTER_TASK, PACK / "turntable_2.mp4", "none")
            print(f"  ({time.time() - t0:.0f} s so far)")
        await browser.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", default=",".join(ALL))
    ap.add_argument("--capture", action="store_true")
    a = ap.parse_args()
    only = [s.strip() for s in a.only.split(",") if s.strip()]
    bad = [s for s in only if s not in ALL]
    if bad:
        raise SystemExit(f"unknown: {bad}; choose from {ALL}")
    if not shutil.which("ffmpeg"):
        raise SystemExit("needs ffmpeg on PATH")
    if a.capture:
        capture()
    for cast in ("quickstart_cast.json", "verify_cast.json"):
        if any(s in only for s in ("seg05", "seg07")) and not (OUTV / cast).exists():
            raise SystemExit(f"out/video/{cast} missing: run with --capture first")
    asyncio.run(run(only))


if __name__ == "__main__":
    main()
