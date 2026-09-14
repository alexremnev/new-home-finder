#!/bin/sh
set -u
JOB="${1:-unknown}"

TOKEN="${TELEGRAM_OPS_TOKEN:-${TELEGRAM_TOKEN:-}}"
[ -z "$TOKEN" ] && exit 0
[ -z "${TELEGRAM_OPS_CHAT:-}" ] && exit 0

FLAG="/tmp/lhf-alerted-$JOB-$(date +%Y%m%d%H)"
[ -f "$FLAG" ] && exit 0
: > "$FLAG"

MSG="/tmp/lhf-alert-$JOB.txt"
{
  echo "Worker failed: $JOB on $(hostname) at $(date -u '+%H:%M UTC')"
  echo
  LOG=$(journalctl -u "london-home-finder@$JOB.service" \
        -u "london-home-finder-$JOB.service" -n 25 --no-pager 2>&1)
  EXCERPT=$(echo "$LOG" | grep -iE "error|traceback|failed|SUMMARY" | tail -12)
  if [ -n "$EXCERPT" ]; then
    echo "$EXCERPT"
  else
    echo "Nothing matched in the journal; it answered:"
    echo "$LOG" | head -3
  fi
  echo
  echo "journalctl -u london-home-finder@$JOB -n 60"
  echo "Further alerts for this job are held for the rest of the hour."
} > "$MSG"

if [ "$(wc -c < "$MSG")" -gt 3500 ]; then
  {
    echo "Worker failed: $JOB on $(hostname) at $(date -u '+%H:%M UTC')"
    echo
    echo "Output too long to quote."
    echo "journalctl -u london-home-finder@$JOB -n 60"
  } > "$MSG"
fi

curl -s -m 20 -o /dev/null -X POST \
  "https://api.telegram.org/bot$TOKEN/sendMessage" \
  --data-urlencode "chat_id=$TELEGRAM_OPS_CHAT" \
  --data-urlencode "text@$MSG"
