#!/usr/bin/env bash
# Runs ON THE VM with sudo: update or roll back to $TAG. Backs up the data first, rebuilds the
# sandbox image only if sandbox/ changed, restarts, checks health. A failed health check rolls
# back to the previous tag on its own.
set -euo pipefail
source /tmp/fc-deploy/common.sh
TAG="${TAG:?}"
OLD=$(cat "$DATA/deployed_tag" 2>/dev/null || git -C "$APP" describe --tags --always)
STAMP=$(date -u +%Y%m%d-%H%M%S)

echo "-- backup ($OLD -> $TAG)"
install -d -o "$FC" -g "$FC" -m 700 "$DATA/backups/$STAMP-$OLD"
for db in history signer; do
  [ -f "$DATA/$db.sqlite3" ] && sqlite3 "$DATA/$db.sqlite3" ".backup '$DATA/backups/$STAMP-$OLD/$db.sqlite3'"
done
chown -R "$FC:$FC" "$DATA/backups"
echo "backup: $DATA/backups/$STAMP-$OLD"

deploy_tag() {
  local to="$1" from="$2"
  as_fc "cd $APP && git fetch -q --tags && git -c advice.detachedHead=false checkout -q $to && git log -1 --oneline"
  as_fc "cd $APP && backend/.venv/bin/pip install -q -e backend"
  build_ui
  if ! as_fc "cd $APP && git diff --quiet $from $to -- sandbox"; then
    echo "sandbox/ changed: rebuilding the image"
    as_fc "cd $APP && sandbox/build.sh"
  fi
  systemctl restart fail-closed-api
  for _ in $(seq 1 60); do curl -fsS --max-time 2 127.0.0.1:8000/health >/dev/null 2>&1 && return 0; sleep 1; done
  return 1
}

if deploy_tag "$TAG" "$OLD"; then
  echo "$TAG" > "$DATA/deployed_tag"
  echo "release $TAG: healthy"
else
  echo "release $TAG: NOT healthy, rolling back to $OLD"
  deploy_tag "$OLD" "$TAG" && echo "rolled back to $OLD: healthy"
  echo "Data restore, only if needed: see docs/deploy.md 'Roll back'"
  exit 1
fi
