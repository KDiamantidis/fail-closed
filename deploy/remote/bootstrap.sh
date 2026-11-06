#!/usr/bin/env bash
# Runs ON THE VM as root, once. A non-root sudo user with the same SSH key, firewall
# (22, 80, 443 only), 2 GB swap for the host, base packages and the system Docker (Caddy only).
set -euo pipefail
OPS="${OPS_USER:-ops}"
export DEBIAN_FRONTEND=noninteractive

echo "-- packages"
apt-get update -q
apt-get -y -q full-upgrade
apt-get -y -q install git curl ca-certificates python3-venv python3-pip uidmap dbus-user-session \
  slirp4netns ufw sqlite3 nano
timedatectl set-timezone UTC

echo "-- user $OPS (sudo without a password; SSH key login only, it has no password)"
if ! id "$OPS" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" "$OPS"
fi
usermod -aG sudo "$OPS"
echo "$OPS ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/90-$OPS"
chmod 440 "/etc/sudoers.d/90-$OPS"
visudo -cf "/etc/sudoers.d/90-$OPS"
install -d -m 700 -o "$OPS" -g "$OPS" "/home/$OPS/.ssh"
install -m 600 -o "$OPS" -g "$OPS" /root/.ssh/authorized_keys "/home/$OPS/.ssh/authorized_keys"

echo "-- swap (host only; sandboxes get none: memory-swap = memory)"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "-- firewall: 22, 80, 443 only"
ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
ufw status verbose

echo "-- system Docker (runs Caddy only; nobody is added to the docker group in rootless mode)"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
  sh /tmp/get-docker.sh >/dev/null
fi
apt-get -y -q install docker-ce-rootless-extras
docker version --format 'docker {{.Server.Version}}'
echo "bootstrap done"
