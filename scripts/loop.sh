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
# ── why drain runs twice a cycle and waits for nothing ───────────────────────
#
# `drain` sends what `ingest` queued, and the gap between the two is latency
# bought with nothing: a listing is in the queue the moment ingest returns, and
# every second after that is a second the subscriber does not have it. So drain
# runs immediately after ingest, in the same breath, and again half a cycle
# later for everything that arrived in between — a retry coming due, a reply
# reopening a WhatsApp window, a notice queued by the web app.
#
# It used to be a single drain offset half a cycle behind ingest, which made the
# offset itself the floor on delivery time. Nothing needed it: drain claims its
# own batch under its own advisory lock, so it is safe to run as often as there
# is anything to send.
#
# ── why a failure does not stop the loop ─────────────────────────────────────
#
# A run that fails is a run; the next one may not. The loop reports and carries on,
# because a container that exits on the first unreachable database turns a five
# minute network problem into an outage lasting until somebody notices.

set -u

INTERVAL="${LOOP_SECONDS:-120}"
HALF=$(( INTERVAL / 2 ))

echo "loop: every ${INTERVAL}s, drain after every ingest and again ${HALF}s later"

while true; do
  START=$(date +%s)

  uv run python -m worker ingest --trigger schedule || echo "loop: ingest failed, carrying on"
  uv run python -m worker drain --trigger schedule || echo "loop: drain failed, carrying on"
  uv run python -m worker rollup --trigger schedule || echo "loop: rollup failed, carrying on"

  # Half-way through the cycle, pick up whatever landed in the queue without an
  # ingest putting it there.
  sleep "$HALF"
  uv run python -m worker drain --trigger schedule || echo "loop: drain failed, carrying on"

  # Sleep the remainder of the interval rather than a fixed amount, so a slow run
  # does not push the schedule later and later. If a cycle overruns, the next one
  # starts immediately instead of accumulating a debt.
  ELAPSED=$(( $(date +%s) - START ))
  REMAINING=$(( INTERVAL - ELAPSED ))
  [ "$REMAINING" -gt 0 ] && sleep "$REMAINING"
done
