"""Sample memory by process family while a run goes (swap investigation, brief 06 phase E).

Every --every seconds, writes one CSV row per family: Blender, Python, the Claude app, Docker,
browsers and everything else, with the summed physical footprint from `top` (the MEM column,
which on Apple silicon includes GPU allocations), plus swap used and free memory. Stops after
--max-s seconds or when <out>.stop exists.

  uv run python scripts/mem_sampler.py --out out/dev_swap/memlog.csv --every 3
"""

import argparse
import csv
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

FAMILIES = (
    ("blender", re.compile(r"blender", re.I)),
    ("python", re.compile(r"python", re.I)),
    ("claude_app", re.compile(r"claude", re.I)),
    ("docker", re.compile(r"docker|com\.docker|virtualization|qemu|vpnkit", re.I)),
    ("browser", re.compile(r"chrome|safari|firefox|webkit", re.I)),
)
UNITS = {"B": 1e-6, "K": 1e-3, "M": 1.0, "G": 1e3, "T": 1e6}


def mb(text: str) -> float:
    m = re.match(r"([\d.]+)([BKMGT])?\+?-?", text.strip())
    if not m:
        return 0.0
    return float(m.group(1)) * UNITS.get(m.group(2) or "B", 1e-6)


def sample() -> dict[str, float]:
    out = subprocess.run(
        ["top", "-l", "1", "-s", "0", "-o", "mem", "-n", "400", "-stats", "pid,mem,command"],
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout
    totals = {name: 0.0 for name, _ in FAMILIES} | {"other": 0.0}
    started = False
    for line in out.splitlines():
        if line.startswith("PID"):
            started = True
            continue
        if not started or not line.strip():
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        _, mem, cmd = parts
        fam = next((name for name, rx in FAMILIES if rx.search(cmd)), "other")
        totals[fam] += mb(mem)
    swap = subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True).stdout
    m = re.search(r"used = ([\d.]+)M", swap)
    totals["swap_used"] = float(m.group(1)) if m else -1.0
    vm = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
    page = 16384
    pm = re.search(r"page size of (\d+) bytes", vm)
    if pm:
        page = int(pm.group(1))
    free = re.search(r"Pages free:\s+(\d+)", vm)
    totals["free_mem"] = int(free.group(1)) * page / 1e6 if free else -1.0
    return {k: round(v, 1) for k, v in totals.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--every", type=float, default=3.0)
    ap.add_argument("--max-s", type=float, default=3600.0)
    args = ap.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    stop = args.out.with_suffix(".stop")
    start = time.monotonic()
    with args.out.open("w", newline="") as fh:
        writer = None
        while time.monotonic() - start < args.max_s and not stop.exists():
            row = {"time": datetime.now().astimezone().isoformat(timespec="seconds")} | sample()
            if writer is None:
                writer = csv.DictWriter(fh, fieldnames=list(row))
                writer.writeheader()
            writer.writerow(row)
            fh.flush()
            time.sleep(args.every)


if __name__ == "__main__":
    main()
