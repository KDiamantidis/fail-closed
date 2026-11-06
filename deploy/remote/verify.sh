#!/usr/bin/env bash
# Runs ON THE VM with sudo. Health, the red team (must be 7/7 contained), no leftover sandbox,
# and the Vultr key and admin token in NO log or data file. Prints counts, never a value.
set -euo pipefail
source /tmp/fc-deploy/common.sh

echo "-- health"
curl -fsS 127.0.0.1:8000/health >/dev/null && echo "API health: OK"
systemctl is-active fail-closed-api

echo "-- red team (Docker mode: $DOCKER_MODE)"
if as_fc "cd $APP/backend && .venv/bin/python cli.py redteam --health-url http://127.0.0.1:8000/health" \
    > /tmp/fc-redteam.txt 2>&1; then
  tail -n 15 /tmp/fc-redteam.txt
  echo "red team: all contained"
else
  tail -n 30 /tmp/fc-redteam.txt
  echo "RED TEAM NOT 7/7 CONTAINED in $DOCKER_MODE mode."
  if [ "$DOCKER_MODE" = rootless ]; then
    echo "Fallback (allowed): deploy.sh <IP> install --docker rootful, then finish and verify again,"
    echo "and say so in README 'Honest limits'."
  fi
  exit 1
fi

echo "-- sandbox containers now (only the warm pool should remain)"
as_fc "docker ps -a --filter label=fail-closed.sandbox --format '{{.Names}} {{.Status}}'"

echo "-- secrets in logs and data files (counts only)"
KEY=$(sed -n 's/^VULTR_INFERENCE_API_KEY=//p' "$APP/.env" | head -n1)
TOK=$(sed -n 's/^ADMIN_TOKEN=//p' "$APP/.env" | head -n1)
found=0
check() {  # check <label> <value>
  local label="$1" v="$2" n=0 j
  [ -n "$v" ] || { echo "$label: not set, skipped"; return; }
  n=$(grep -rlF -- "$v" /var/log "$DATA" "$APP/data" /opt/caddy /tmp/fc-redteam.txt 2>/dev/null | wc -l || true)
  j=$(journalctl --no-pager -q 2>/dev/null | grep -cF -- "$v" || true)
  c=$(cd /opt/caddy && docker compose logs --no-color 2>/dev/null | grep -cF -- "$v" || true)
  echo "$label: files $n, journal lines $j, Caddy log lines $c"
  [ "$n" = 0 ] && [ "$j" = 0 ] && [ "$c" = 0 ] || found=1
}
echo "-- save a red-team report for the Containment page (admin action, token read here, never printed)"
curl -fsS -o /dev/null --max-time 600 -X POST -H @<(printf 'X-Admin-Token: %s\n' "$TOK") -H "Accept: text/event-stream"   127.0.0.1:8000/redteam/run && curl -fsS 127.0.0.1:8000/redteam/last | python3 -c   'import json,sys; r=json.load(sys.stdin); print("Containment page report:", sum(x["contained"] for x in r["results"]), "of", len(r["results"]), "contained")'

check "Vultr key" "$KEY"
check "admin token" "$TOK"
unset KEY TOK
[ "$found" = 0 ] && echo "no secret in any log: OK" || { echo "ERROR: a secret was found in a log"; exit 1; }
rm -f /tmp/fc-redteam.txt
