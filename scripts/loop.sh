#!/bin/sh
# Run ingest and drain for ever, for hosts that keep a process alive rather than
# firing a timer.
#
#   docker run … --entrypoint /app/scripts/loop.sh <image>
#
# ── why a loop and not the container's own scheduler ─────────────────────────
#
# Platforms that bill per running process (Fly, Railway, a plain VPS with systemd)
# want one process that does not exit. Platforms that bill per invocation want a
# cron. This is the first kind; a cron entry calling `python -m worker ingest` is
# the second, and both drive the same code.
#
# ── why the two jobs are offset ──────────────────────────────────────────────
#
# `drain` sends what `ingest` queued, so running them together means every batch
# waits a full cycle before going out. Half a cycle apart is the shortest honest
# delay between a listing appearing and it arriving.
#
# ── why a failure does not stop the loop ─────────────────────────────────────
#
# A run that fails is a run; the next one may not. The loop reports and carries on,
# because a container that exits on the first unreachable database turns a five
# minute network problem into an outage lasting until somebody notices.

set -u

INTERVAL="${LOOP_SECONDS:-120}"
HALF=$(( INTERVAL / 2 ))

echo "loop: every ${INTERVAL}s, drain offset by ${HALF}s"

while true; do
  START=$(date +%s)

  uv run python -m worker ingest --trigger schedule || echo "loop: ingest failed, carrying on"
  sleep "$HALF"
  uv run python -m worker drain --trigger schedule || echo "loop: drain failed, carrying on"

  # Sleep the remainder of the interval rather than a fixed amount, so a slow run
  # does not push the schedule later and later. If a cycle overruns, the next one
  # starts immediately instead of accumulating a debt.
  ELAPSED=$(( $(date +%s) - START ))
  REMAINING=$(( INTERVAL - ELAPSED ))
  [ "$REMAINING" -gt 0 ] && sleep "$REMAINING"
done
