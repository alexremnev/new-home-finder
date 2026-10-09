#!/bin/bash
# Run one worker job by hand, with the settings the timers use.
#
# The settings live in /etc/london-home-finder.env and systemd injects them
# through `EnvironmentFile=`. `sudo -u finder .../python -m worker portals`
# does not go through systemd, so nothing is injected and the job stops on
# "DATABASE_URL is not set" — which is the config loader being right, not a
# fault. The loader also looks for a `.env` beside the code, and deliberately
# there is none: the server keeps its secrets outside the git checkout.
#
# So: read that file, then drop to the service account with it. Same file, same
# job, same account as a timed run — which is the point, because a manual run
# that reads a different configuration proves nothing about the real one.
#
# Usage, as root:
#
#   bash deploy/run-job.sh rightmove --dry-run
#   bash deploy/run-job.sh zoopla
#   bash deploy/run-job.sh zoopla_london    # the whole city in one search
#   bash deploy/run-job.sh spareroom        # a slice of the SpareRoom feed
#   bash deploy/run-job.sh openrent
#   bash deploy/run-job.sh portals          # every reader in one go
#   bash deploy/run-job.sh drain
#
# A run with no flags can equally be `systemctl start london-home-finder@zoopla`,
# which is the same thing through systemd; this script exists for the runs that
# need flags, since a unit's ExecStart is fixed.

set -euo pipefail

DIR=/opt/london-home-finder
ENV_FILE=/etc/london-home-finder.env
USER_NAME=finder

if [ "$#" -eq 0 ]; then
  echo "Usage: bash deploy/run-job.sh <job> [flags]" >&2
  echo "Jobs: ingest, rightmove, zoopla, zoopla_london," >&2
  echo "      spareroom, openrent, portals," >&2
  echo "      drain, rollup, scrape" >&2
  exit 2
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo bash deploy/run-job.sh $*" >&2
  exit 1
fi

[ -f "$ENV_FILE" ] || { echo "No $ENV_FILE. Run deploy/install.sh first." >&2; exit 1; }

# `set -a` exports everything the file defines, which is what the job needs and
# what `EnvironmentFile=` does. Sourced in this shell and not exported further
# than the one command below.
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

# `runuser` rather than `sudo -u`: sudo strips the environment by design and
# putting it back with -E means arguing with its policy on every variable.
# --trigger manual so the run log distinguishes this from a timed one.
exec runuser -u "$USER_NAME" --preserve-environment -- \
  "$DIR/.venv/bin/python" -m worker "$@" --trigger manual
