#!/bin/sh
# Run the scenarios (Linux / WSL2 / the Vultr VM).
#   scenarios/run.sh                  # run-all: every scenario, one table + breaker state
#   scenarios/run.sh d_binance_7702   # one scenario with the full report
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
root="$(dirname "$here")"

if ! docker image inspect fail-closed-sandbox:foundry-v1.8.5 >/dev/null 2>&1; then
  echo "Sandbox image missing. Build it first: sandbox/build.sh"; exit 1
fi
if [ ! -x "$root/backend/.venv/bin/python" ]; then
  echo "Creating backend/.venv ..."
  python3 -m venv "$root/backend/.venv"
  "$root/backend/.venv/bin/python" -m pip install -q -e "$root/backend"
fi
py="$root/backend/.venv/bin/python"
if [ $# -eq 0 ]; then
  exec "$py" "$root/backend/cli.py" run-all
fi
exec "$py" "$root/backend/cli.py" scenario "$here/${1%.json}.json"
