#!/usr/bin/env bash
# Runs ON THE VM with sudo. The limited user "failclosed", its Docker (rootless by default,
# DOCKER_MODE=rootful is the fallback), the code at $TAG, the sandbox image, the web UI, the
# .env template (secrets left empty for you), the systemd unit and the Caddy files.
# Safe to run again.
set -euo pipefail
source /tmp/fc-deploy/common.sh
TAG="${TAG:?}"; DOMAIN="${DOMAIN:?}"
REPO_URL="https://github.com/KDiamantidis/fail-closed.git"

echo "-- user $FC"
id "$FC" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash "$FC"
loginctl enable-linger "$FC"
install -d -o "$FC" -g "$FC" -m 700 "$DATA" /opt/fail-closed
UID_FC=$(fc_uid)
for _ in $(seq 1 20); do [ -d "/run/user/$UID_FC" ] && break; sleep 0.5; done

if [ "$DOCKER_MODE" = rootless ]; then
  echo "-- rootless Docker for $FC (sandboxes only)"
  gpasswd -d "$FC" docker >/dev/null 2>&1 || true   # never in the root-equivalent group here
  mkdir -p /etc/systemd/system/user@.service.d
  cat > /etc/systemd/system/user@.service.d/delegate.conf <<'EOF'
[Service]
Delegate=cpu cpuset io memory pids
EOF
  # Ubuntu 24.04 limits unprivileged user namespaces with AppArmor; rootlesskit needs this profile.
  cat > /etc/apparmor.d/usr.bin.rootlesskit <<'EOF'
abi <abi/4.0>,
include <tunables/global>

/usr/bin/rootlesskit flags=(unconfined) {
  userns,

  include if exists <local/usr.bin.rootlesskit>
}
EOF
  systemctl restart apparmor
  systemctl daemon-reload
  systemctl restart "user@$UID_FC.service"
  sleep 2
  as_fc "dockerd-rootless-setuptool.sh install >/dev/null"
  as_fc "systemctl --user enable --now docker"
  as_fc "docker info --format 'rootless: {{.SecurityOptions}} | cgroup v{{.CgroupVersion}} ({{.CgroupDriver}})'"
  CTRL="/sys/fs/cgroup/user.slice/user-$UID_FC.slice/user@$UID_FC.service/cgroup.controllers"
  echo "delegated cgroup controllers: $(cat "$CTRL")"
  for c in cpu memory pids; do
    grep -qw "$c" "$CTRL" || { echo "ERROR: cgroup controller $c not delegated: sandbox limits would not hold"; exit 1; }
  done
else
  echo "-- FALLBACK: sandboxes on the system Docker daemon"
  echo "   $FC joins the docker group, which is root-equivalent. State this in README 'Honest limits'."
  usermod -aG docker "$FC"
fi

echo "-- code at $TAG"
if [ ! -d "$APP/.git" ]; then
  as_fc "git clone -q $REPO_URL $APP"
fi
as_fc "cd $APP && git fetch -q --tags && git -c advice.detachedHead=false checkout -q $TAG && git log -1 --oneline"
install -d -o "$FC" -g "$FC" -m 700 "$APP/data"

echo "-- sandbox image (in the daemon the API uses)"
as_fc "cd $APP && sandbox/build.sh"

echo "-- Python"
as_fc "cd $APP && python3 -m venv backend/.venv && backend/.venv/bin/pip install -q --upgrade pip && backend/.venv/bin/pip install -q -e backend"

echo "-- web UI"
build_ui

echo "-- .env (the two secrets stay EMPTY: you type them in the next step)"
ENVF="$APP/.env"
if [ ! -f "$ENVF" ]; then
  install -o "$FC" -g "$FC" -m 600 "$APP/.env.example" "$ENVF"
fi
chown "$FC:$FC" "$ENVF"; chmod 600 "$ENVF"
set_env() {  # set_env KEY VALUE: replace the line, or append it
  if grep -q "^$1=" "$ENVF"; then
    sed -i "s|^$1=.*|$1=$2|" "$ENVF"
  else
    echo "$1=$2" >> "$ENVF"
  fi
}
set_env LLM_MODE live
set_env GUARD_KEY_PATH "$DATA/guard_ed25519.pem"
set_env HISTORY_DB "$DATA/history.sqlite3"
set_env SIGNER_DB "$DATA/signer.sqlite3"
set_env SANDBOX_MODE warm
set_env REDTEAM_PUBLIC false
set_env PUBLIC_DEMO true
set_env DEPLOY_TARGET vm
set_env TRUSTED_PROXY caddy
set_env LIVE_UPDATES sse
set_env LLM_DAILY_BUDGET_USD 1
set_env LLM_BUDGET_FILE "$DATA/llm_budget.json"
set_env CORS_ORIGINS "[\"https://$DOMAIN\"]"
grep -q '^VULTR_INFERENCE_API_KEY=' "$ENVF" || echo 'VULTR_INFERENCE_API_KEY=' >> "$ENVF"
grep -q '^ADMIN_TOKEN=' "$ENVF" || echo 'ADMIN_TOKEN=' >> "$ENVF"

echo "-- systemd unit (API on the host, user $FC, 127.0.0.1:8000)"
if [ "$DOCKER_MODE" = rootless ]; then
  DOCKER_ENV="Environment=DOCKER_HOST=unix:///run/user/$UID_FC/docker.sock
Environment=XDG_RUNTIME_DIR=/run/user/$UID_FC"
  AFTER="user@$UID_FC.service"
else
  DOCKER_ENV=""
  AFTER="docker.service"
fi
cat > /etc/systemd/system/fail-closed-api.service <<EOF
[Unit]
Description=Fail Closed Guard API
After=network-online.target $AFTER
Wants=network-online.target

[Service]
User=$FC
Group=$FC
WorkingDirectory=$APP/backend
$DOCKER_ENV
ExecStart=$APP/backend/.venv/bin/uvicorn app.api:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers --log-level warning
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$DATA $APP/data
PrivateTmp=true
UMask=0077

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable fail-closed-api >/dev/null

echo "-- Caddy files (started in 'finish')"
install -d -m 755 /opt/caddy
install -m 644 /tmp/fc-deploy/Caddyfile /opt/caddy/Caddyfile
install -m 644 /tmp/fc-deploy/docker-compose.yml /opt/caddy/docker-compose.yml
echo "DOMAIN=$DOMAIN" > /opt/caddy/.env
echo "$TAG" > "$DATA/deployed_tag"; chown "$FC:$FC" "$DATA/deployed_tag"
echo "install done (Docker mode: $DOCKER_MODE)"
