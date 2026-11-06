#!/usr/bin/env bash
# Runs ON THE VM with sudo, after you typed the secrets. Checks that both are filled in
# (prints only "set" / "MISSING", never a value), then starts the API and Caddy.
set -euo pipefail
source /tmp/fc-deploy/common.sh
DOMAIN="${DOMAIN:?}"
ENVF="$APP/.env"

awk -F= '
  /^VULTR_INFERENCE_API_KEY=/ { k = length(substr($0, index($0, "=") + 1)) }
  /^ADMIN_TOKEN=/             { t = length(substr($0, index($0, "=") + 1)) }
  END {
    print (k > 0 ? "Vultr key: set" : "Vultr key: MISSING")
    print (t >= 32 ? "admin token: set" : "admin token: MISSING or shorter than 32 characters")
    exit (k > 0 && t >= 32) ? 0 : 1
  }' "$ENVF" || { echo "Fill in both lines (deploy.sh <IP> secrets), then run finish again."; exit 1; }
[ "$(stat -c '%U %a' "$ENVF")" = "$FC 600" ] || { chown "$FC:$FC" "$ENVF"; chmod 600 "$ENVF"; }
git -C "$APP" status --porcelain | grep -q '\.env$' && { echo "ERROR: .env is not ignored by git"; exit 1; }

echo "-- start the API"
systemctl restart fail-closed-api
for _ in $(seq 1 60); do curl -fsS --max-time 2 127.0.0.1:8000/health >/dev/null 2>&1 && break; sleep 1; done
curl -fsS 127.0.0.1:8000/health >/dev/null && echo "API health: OK"
curl -fsS 127.0.0.1:8000/status | python3 -c 'import json,sys; s=json.load(sys.stdin); print("LLM", s["llm"], s["model"], "| public demo", s["public_demo"], "| budget/day $", s["llm_budget"]["limit_usd"], "| sandbox", s["sandbox_mode"])'
[ -f "$DATA/guard_ed25519.pem" ] && echo "Guard key: $(stat -c '%U %a' "$DATA/guard_ed25519.pem") (owner-only)"

echo "-- start Caddy (HTTPS for $DOMAIN)"
cd /opt/caddy && docker compose up -d --quiet-pull
for _ in $(seq 1 90); do curl -fsS --max-time 5 "https://$DOMAIN/health" >/dev/null 2>&1 && break; sleep 2; done
curl -fsS --max-time 10 "https://$DOMAIN/health" >/dev/null && echo "HTTPS https://$DOMAIN: OK" \
  || { echo "HTTPS not ready yet: see 'docker compose -f /opt/caddy/docker-compose.yml logs'"; exit 1; }
