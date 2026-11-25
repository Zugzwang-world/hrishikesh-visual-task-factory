"""Run a command in a pseudo-terminal and save its real output with timing as a cast file.

    uv run python scripts/record_screens/pty_cast.py --out out/video/quickstart_cast.json \
        --cols 118 --rows 30 -- uv run vtf quickstart

The cast is JSON: {"version": 1, "cols", "rows", "command", "started", "duration", "exit_code",
"events": [[seconds since start, text], ...]}. Text is the raw terminal stream (colours and
cursor moves included), decoded as UTF-8. Nothing is filtered or rewritten, so a replay shows
exactly what the command printed and when. Output is also echoed to this process's stdout.
"""

import argparse
import codecs
import fcntl
import json
import os
import pty
import select
import shlex
import struct
import sys
import termios
import time
from datetime import UTC, datetime


def capture(cmd: list[str], cols: int, rows: int, env: dict[str, str]) -> dict:
    pid, fd = pty.fork()
    if pid == 0:  # child
        os.execvpe(cmd[0], cmd, env)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    events: list[list] = []
    t0 = time.monotonic()
    started = datetime.now(UTC).isoformat(timespec="seconds")
    while True:
        try:
            ready, _, _ = select.select([fd], [], [], 0.5)
        except InterruptedError:
            continue
        if not ready:
            done, _status = os.waitpid(pid, os.WNOHANG)
            if done:
                break
            continue
        try:
            data = os.read(fd, 65536)
        except OSError:  # EIO: the child closed the terminal
            break
        if not data:
            break
        text = decoder.decode(data)
        if text:
            events.append([round(time.monotonic() - t0, 4), text])
            sys.stdout.write(text)
            sys.stdout.flush()
    _, status = os.waitpid(pid, 0)
    tail = decoder.decode(b"", final=True)
    if tail:
        events.append([round(time.monotonic() - t0, 4), tail])
    return {
        "version": 1,
        "cols": cols,
        "rows": rows,
        "command": shlex.join(cmd),
        "started": started,
        "duration": round(time.monotonic() - t0, 3),
        "exit_code": os.waitstatus_to_exitcode(status),
        "events": events,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--cols", type=int, default=118)
    ap.add_argument("--rows", type=int, default=30)
    ap.add_argument("--show", default=None, help="Command as shown on screen (default: argv).")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    env = dict(os.environ, COLUMNS=str(a.cols), LINES=str(a.rows), TERM="xterm-256color")
    cast = capture(cmd, a.cols, a.rows, env)
    if a.show:
        cast["command"] = a.show
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(cast, f)
    print(f"\n[pty_cast] {len(cast['events'])} events, {cast['duration']} s, "
          f"exit {cast['exit_code']} -> {a.out}", file=sys.stderr)  # fmt: skip
    sys.exit(cast["exit_code"])


if __name__ == "__main__":
    main()
