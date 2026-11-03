#!/bin/sh
# Run a scenario end to end (Linux / WSL2 / the Vultr VM).
#   scenarios/run.sh               # 7702_attack
#   scenarios/run.sh 7702_attack --json
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
root="$(dirname "$here")"
scenario="${1:-7702_attack}"
[ $# -gt 0 ] && shift

if ! docker image inspect fail-closed-sandbox:foundry-v1.8.5 >/dev/null 2>&1; then
  echo "Sandbox image missing. Build it first: sandbox/build.sh"; exit 1
fi
if [ ! -x "$root/backend/.venv/bin/python" ]; then
  echo "Creating backend/.venv ..."
  python3 -m venv "$root/backend/.venv"
  "$root/backend/.venv/bin/python" -m pip install -q -e "$root/backend"
fi
exec "$root/backend/.venv/bin/python" "$root/backend/cli.py" scenario "$here/$scenario" "$@"
