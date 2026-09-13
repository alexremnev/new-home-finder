#!/bin/bash
set -euo pipefail

REPO="${REPO:-}"
DIR=/opt/london-home-finder
ENV_FILE=/etc/london-home-finder.env
USER_NAME=finder

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo -E bash deploy/install.sh" >&2
  exit 1
fi

echo "== packages"
apt-get update -qq
apt-get install -y -qq curl git ca-certificates unattended-upgrades

echo "== user"
id -u "$USER_NAME" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$USER_NAME"

echo "== code"
git config --global --add safe.directory "$DIR" 2>/dev/null || true

if [ -d "$DIR/.git" ]; then
  BRANCH="$(git -C "$DIR" rev-parse --abbrev-ref HEAD)"
  git -C "$DIR" fetch --quiet origin "$BRANCH"
  git -C "$DIR" reset --hard --quiet "origin/$BRANCH"
  echo "   on $BRANCH at $(git -C "$DIR" rev-parse --short HEAD)"
else
  [ -n "$REPO" ] || {
    echo "No checkout at $DIR." >&2
    echo "Either clone it there first — which a private repository needs, so that" >&2
    echo "the deploy key is used — or pass REPO=<git url> for a public one." >&2
    exit 1
  }
  git clone --quiet "$REPO" "$DIR"
fi
mkdir -p "$DIR/reports"

echo "== python"
export UV_INSTALL_DIR=/usr/local/bin
command -v uv >/dev/null 2>&1 || curl -fsSL https://astral.sh/uv/install.sh | sh
cd "$DIR"
uv sync --no-dev --extra ingest
chown -R "$USER_NAME:$USER_NAME" "$DIR"

echo "== settings"
if [ ! -f "$ENV_FILE" ]; then
  cp "$DIR/.env.example" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  chown root:"$USER_NAME" "$ENV_FILE"
  echo
  echo "   $ENV_FILE was created from the example and is EMPTY."
  echo "   Fill it in, then run this script again."
  echo
  exit 0
fi
chmod 600 "$ENV_FILE"
chown root:"$USER_NAME" "$ENV_FILE"

echo "== timers"
cp "$DIR"/deploy/systemd/*.service "$DIR"/deploy/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
for job in ingest drain rollup report; do
  systemctl enable --now "london-home-finder-$job.timer"
done

echo "== firewall"
if command -v ufw >/dev/null 2>&1; then
  ufw --force reset >/dev/null
  ufw default deny incoming >/dev/null
  ufw default allow outgoing >/dev/null
  ufw allow 22/tcp >/dev/null
  ufw --force enable >/dev/null
fi

echo
systemctl list-timers 'london-home-finder-*' --no-pager
echo
echo "Nothing listens on this machine: the worker only makes outbound"
echo "connections, so the firewall allows nothing in but ssh."
