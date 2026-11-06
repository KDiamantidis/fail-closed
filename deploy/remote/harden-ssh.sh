#!/usr/bin/env bash
# Runs ON THE VM with sudo, after the ops user was proven to log in with the key.
# No root login, no passwords. The 00- file wins over cloud-init's 50-cloud-init.conf.
set -euo pipefail
cat > /etc/ssh/sshd_config.d/00-fail-closed.conf <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
EOF
sshd -t
systemctl reload ssh
sshd -T | grep -E '^(permitrootlogin|passwordauthentication|kbdinteractiveauthentication) '
