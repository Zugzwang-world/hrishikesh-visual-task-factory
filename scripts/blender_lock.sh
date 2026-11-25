#!/usr/bin/env bash
# One Blender process at a time on this 24 GB Mac (each Cycles render takes about 6.6 GB).
# Waits for the shared lock in out/logs/blender.lock, logs disk, swap and free memory, refuses
# to start below the 20 GB disk floor, runs the command, then releases the lock.
#
#   scripts/blender_lock.sh <label> -- <command ...>
#
# out/ is shared by every worktree (each worktree symlinks out -> the main checkout's out/), so
# the lock is too. A lock whose holder process is gone is cleared.
set -euo pipefail
LABEL=${1:?label}
shift
if [ "${1:-}" = "--" ]; then shift; fi
ROOT=$(cd "$(dirname "$0")/.." && pwd)
LOGS=${VTF_LOGS:-$ROOT/out/logs}
LOCK=$LOGS/blender.lock
FLOOR_GB=${VTF_DISK_FLOOR_GB:-20}
mkdir -p "$LOGS"

waited=0
until mkdir "$LOCK" 2>/dev/null; do
  holder=$(cat "$LOCK/pid" 2>/dev/null || true)
  if [ -n "$holder" ] && ! kill -0 "$holder" 2>/dev/null; then
    rm -rf "$LOCK"
    continue
  fi
  if [ $((waited % 300)) -eq 0 ]; then
    echo "[$(date '+%F %T')] $LABEL: waiting for the Blender lock ($(cat "$LOCK/label" 2>/dev/null || echo '?'))"
  fi
  sleep 15
  waited=$((waited + 15))
done
echo $$ >"$LOCK/pid"
echo "$LABEL" >"$LOCK/label"
trap 'rm -rf "$LOCK"' EXIT

free_gb=$(df -g "$ROOT/out/" | awk 'NR==2{print $4}')
swap=$(sysctl -n vm.swapusage | sed 's/  */ /g')
page=$(sysctl -n hw.pagesize)
avail_gb=$(vm_stat | awk -v p="$page" '/Pages free|Pages inactive|Pages speculative/{gsub(/\./,"",$NF); s+=$NF} END{printf "%.1f", s*p/1e9}')
echo "[$(date '+%F %T')] $LABEL: start; disk free ${free_gb} GB; swap ${swap}; free+inactive ${avail_gb} GB" | tee -a "$LOGS/blender_lock.log"
if [ "$free_gb" -lt "$FLOOR_GB" ]; then
  echo "[$(date '+%F %T')] $LABEL: below the ${FLOOR_GB} GB disk floor; not starting" | tee -a "$LOGS/blender_lock.log" >&2
  exit 3
fi
status=0
"$@" || status=$?
echo "[$(date '+%F %T')] $LABEL: done (exit $status)" | tee -a "$LOGS/blender_lock.log"
exit $status
