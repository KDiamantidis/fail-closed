#!/usr/bin/env bash
# Shared by the remote scripts (sourced). Runs as root (via sudo).
FC=failclosed
APP=/opt/fail-closed/app
DATA=/var/lib/fail-closed
DOCKER_MODE="${DOCKER_MODE:-rootless}"

fc_uid() { id -u "$FC"; }

# Run a command as failclosed, talking to the Docker daemon the API uses.
as_fc() {
  local uid; uid=$(fc_uid)
  if [ "$DOCKER_MODE" = rootless ]; then
    sudo -u "$FC" -H env XDG_RUNTIME_DIR="/run/user/$uid" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" \
      DOCKER_HOST="unix:///run/user/$uid/docker.sock" PATH="/usr/bin:/bin:/usr/sbin:/sbin" bash -lc "$*"
  else
    sudo -u "$FC" -H env PATH="/usr/bin:/bin:/usr/sbin:/sbin" bash -lc "$*"
  fi
}

# Build the web UI in a throwaway Node container (no Node on the host).
build_ui() {
  if [ "$DOCKER_MODE" = rootless ]; then  # container root = failclosed on the host
    as_fc "cd $APP && docker run --rm -v \$PWD/frontend:/w -w /w node:22-slim sh -c 'npm ci --no-audit --no-fund && npm run build'"
  else
    as_fc "cd $APP && docker run --rm --user \$(id -u):\$(id -g) -e HOME=/tmp -v \$PWD/frontend:/w -w /w node:22-slim sh -c 'npm ci --no-audit --no-fund && npm run build'"
  fi
  test -f "$APP/frontend/dist/index.html"
}
