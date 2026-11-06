#!/usr/bin/env bash
# Deploy Fail Closed to a fresh Vultr VM (Ubuntu 24.04), from this laptop, over SSH.
#
#   deploy/deploy.sh <IP> bootstrap        as root: user "ops" (sudo, your key), firewall, swap, Docker;
#                                          then root SSH login and password login are turned off
#   deploy/deploy.sh <IP> install          as ops: user "failclosed", rootless Docker, code at the tag,
#                                          sandbox image, web UI, .env template, systemd + Caddy
#                                          (--docker rootful: the fallback, see docs/deploy.md)
#   deploy/deploy.sh <IP> secrets          prints the ONE command you run yourself to type the secrets
#   deploy/deploy.sh <IP> finish           checks the secrets are set (never prints them), starts it all
#   deploy/deploy.sh <IP> verify           health, red team, replays (d) and (g) over HTTPS, no key in logs
#   deploy/deploy.sh <IP> all              all of the above, pausing at "secrets" until you press Enter
#   deploy/deploy.sh <IP> release <tag>    update (or roll back) to <tag>; backs up the data first
#
# Secrets (Vultr key, ADMIN_TOKEN) never pass through this script or its output.
set -euo pipefail

IP="${1:?usage: deploy/deploy.sh <IP> <step> [args]}"
STEP="${2:?step: bootstrap | install | secrets | finish | verify | all | release <tag>}"
shift 2
TAG="${TAG:-v1.2-hackathon}"
KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}"
OPS="${OPS_USER:-ops}"
DOCKER_MODE="${DOCKER_MODE:-rootless}"
DOMAIN="${IP}.sslip.io"
HERE="$(cd "$(dirname "$0")" && pwd)"
SSH_OPTS=(-i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15)

while [ $# -gt 0 ]; do
  case "$1" in
    --docker) DOCKER_MODE="$2"; shift 2 ;;
    *) EXTRA="$1"; shift ;;
  esac
done
case "$DOCKER_MODE" in rootless|rootful) ;; *) echo "--docker rootless|rootful"; exit 2 ;; esac
[[ "$IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "IP must be an IPv4 address"; exit 2; }

say() { printf '\n== %s\n' "$*"; }
as_root() { ssh "${SSH_OPTS[@]}" "root@$IP" "$@"; }
as_ops() { ssh "${SSH_OPTS[@]}" "$OPS@$IP" "$@"; }
push() {  # push <user> <files...>: copy the remote scripts to /tmp/fc-deploy
  local user="$1"; shift
  ssh "${SSH_OPTS[@]}" "$user@$IP" "rm -rf /tmp/fc-deploy && mkdir -p /tmp/fc-deploy"
  scp -q "${SSH_OPTS[@]}" "$@" "$user@$IP:/tmp/fc-deploy/"
  ssh "${SSH_OPTS[@]}" "$user@$IP" "sed -i 's/\r\$//' /tmp/fc-deploy/*"  # Windows line endings
}

bootstrap() {
  say "bootstrap as root on $IP (user $OPS, firewall 22/80/443, swap, Docker)"
  push root "$HERE/remote/bootstrap.sh"
  as_root "OPS_USER=$OPS bash /tmp/fc-deploy/bootstrap.sh"
  say "check that $OPS can log in and use sudo, before root login is turned off"
  as_ops "sudo -n true && echo 'ops login + sudo: OK'"
  say "turn off root SSH login and password login"
  push "$OPS" "$HERE/remote/harden-ssh.sh"
  as_ops "sudo bash /tmp/fc-deploy/harden-ssh.sh"
  as_ops "echo 'still reachable as $OPS: OK'"
  if ssh "${SSH_OPTS[@]}" -o BatchMode=yes "root@$IP" true 2>/dev/null; then
    echo "ERROR: root can still log in"; exit 1
  fi
  echo "root SSH login refused: OK"
}

install() {
  say "install as $OPS (Docker mode: $DOCKER_MODE, tag $TAG, domain $DOMAIN)"
  push "$OPS" "$HERE/remote/install.sh" "$HERE/remote/common.sh" "$HERE/Caddyfile" "$HERE/docker-compose.yml"
  as_ops "sudo TAG=$TAG DOMAIN=$DOMAIN DOCKER_MODE=$DOCKER_MODE bash /tmp/fc-deploy/install.sh"
}

secrets() {
  cat <<EOF

== YOUR TURN: type the secrets on the VM yourself
Open YOUR OWN terminal (PowerShell or Git Bash; not the Claude Code prompt, not with "!").
ssh uses your key ~/.ssh/id_ed25519 by default. Run:

  ssh -t $OPS@$IP "sudo -u failclosed nano /opt/fail-closed/app/.env"

In nano, fill in these two lines (everything else is already set):

  VULTR_INFERENCE_API_KEY=<your key from the Vultr console>
  ADMIN_TOKEN=<a new random value>

  For ADMIN_TOKEN, in that same terminal you can first run:
  ssh $OPS@$IP "openssl rand -hex 32"
  and paste the result. Keep the token somewhere safe: it lifts a real breaker level.

Save with Ctrl+O, Enter, then Ctrl+X. Then tell Claude "secrets done".
Nothing here sends the values anywhere; the next step only checks that both lines are filled.
EOF
}

finish() {
  say "check the secrets are set (values are never printed), then start the API and Caddy"
  push "$OPS" "$HERE/remote/finish.sh" "$HERE/remote/common.sh"
  as_ops "sudo DOMAIN=$DOMAIN DOCKER_MODE=$DOCKER_MODE bash /tmp/fc-deploy/finish.sh"
}

verify() {
  say "verify on the VM: health, red team, no key or token in any log"
  push "$OPS" "$HERE/remote/verify.sh" "$HERE/remote/common.sh"
  as_ops "sudo DOCKER_MODE=$DOCKER_MODE bash /tmp/fc-deploy/verify.sh"
  say "verify from here, over HTTPS: https://$DOMAIN"
  local base="https://$DOMAIN"
  curl -fsS --max-time 20 "$base/health" >/dev/null && echo "HTTPS + certificate: OK"
  curl -fsS --max-time 10 -o /dev/null -w 'HTTP on port 80 -> %{http_code} (redirect to HTTPS expected)\n' \
    "http://$DOMAIN/health" || true
  local d g
  d=$(curl -fsS --max-time 120 -X POST "$base/scenarios/d_delegation_drain/run")
  echo "$d" | grep -q '"verdict":"BLOCK"' && echo "replay (d) delegation drain: BLOCK, nothing signed: OK" \
    || { echo "replay (d): unexpected result"; exit 1; }
  g=$(curl -fsS --max-time 300 -X POST "$base/scenarios/g_coordinated_breaker/run")
  echo "$g" | grep -q '"global_stop":{' && echo "replay (g) coordinated attack: reaches the global stop: OK" \
    || { echo "replay (g): did not reach level 4"; exit 1; }
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$base/check" -H 'Content-Type: application/json' -d '{}')
  [ "$code" = "401" ] && echo "public demo: raw /check needs the admin token (401): OK" \
    || { echo "public demo: /check answered $code, expected 401"; exit 1; }
  echo
  echo "Open $base in a browser: run (d) and (g), check the colour strip, Reset demo, and the admin lift."
}

release() {
  local to="${EXTRA:?release needs a tag: deploy/deploy.sh <IP> release <tag>}"
  say "release $to (backup first, then build and restart)"
  push "$OPS" "$HERE/remote/release.sh" "$HERE/remote/common.sh"
  as_ops "sudo TAG=$to DOCKER_MODE=$DOCKER_MODE bash /tmp/fc-deploy/release.sh"
  curl -fsS --max-time 20 "https://$DOMAIN/health" >/dev/null && echo "HTTPS health after release: OK"
}

case "$STEP" in
  bootstrap) bootstrap ;;
  install) install ;;
  secrets) secrets ;;
  finish) finish ;;
  verify) verify ;;
  release) release ;;
  all)
    bootstrap; install; secrets
    read -r -p "Press Enter when you have saved the .env on the VM... " _
    finish; verify ;;
  *) echo "unknown step $STEP"; exit 2 ;;
esac
