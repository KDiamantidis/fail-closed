# Deploy plan: Vultr VM (not deployed yet)

Target: **Ubuntu 24.04, 2 vCPU / 4 GB RAM, Stockholm, SSH key login.**
Status: plan only. Nothing here has been run on the VM yet.

## Layout

```
Internet ──443/80──► Caddy (Docker Compose, rootful Docker, network_mode: host)
                        │  HTTPS, automatic certificate
                        ▼
                     127.0.0.1:8000  Guard API (uvicorn, systemd, user "failclosed", on the HOST)
                        │  Docker SDK, the failclosed user's OWN rootless Docker daemon
                        ▼
                     sandbox containers (no network, read-only, 512 MB, 1 CPU, 128 pids, all caps dropped)
```

- **No `docker.sock` in any container.** Caddy only proxies HTTP. The API is not a container: it
  runs on the host as the limited user `failclosed` and starts sandboxes itself.
- **Two Docker daemons, on purpose.** The system (rootful) daemon runs only Caddy. The
  `failclosed` user's rootless daemon runs only sandboxes. The API can never start a privileged
  container, and Docker access is not root on the host.
- **Sandbox limits unchanged**: `--network none`, read-only root, tmpfs `/tmp` noexec, 512 MB with
  no swap, 1 CPU, 128 pids, `--cap-drop ALL`, `no-new-privileges`, non-root user, timeout. Rootless
  Docker enforces memory / CPU / pids only with cgroup v2 delegation (step 4); the red-team run in
  step 10 proves it, and **we do not go live unless it reports 7/7 contained**.
- Secrets: `.env` (Vultr key, admin token) is copied by hand, owner-only, never in git. The Guard
  key is created on first start in `/var/lib/fail-closed`, owner-only. No secret ever enters a sandbox.

## Domain

Proposal: **`<ip-with-dashes>.sslip.io`**, for example `203-0-113-7.sslip.io`. It resolves to the
VM's IP with no DNS setup and no cost, and Caddy gets a real certificate for it. If the
certificate authority refuses it (rate limits on shared domains), Caddy falls back to ZeroSSL on
its own; if both fail, a cheap domain with one A record works with the same Caddyfile.

## Steps

Run as root (or with sudo) unless a step says `failclosed`. `<IP>` is the VM's public IPv4,
`<HOST>` the domain (e.g. `203-0-113-7.sslip.io`).

### 1. Base system and firewall

```sh
apt update && apt -y full-upgrade
apt -y install git python3-venv python3-pip uidmap dbus-user-session slirp4netns ufw sqlite3
timedatectl set-timezone UTC

# 2 GB swap for the host (sandboxes still get no swap: memory-swap = memory)
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

ufw default deny incoming && ufw default allow outgoing
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp
ufw enable && ufw status verbose
```

Also in the Vultr console: a **firewall group** with inbound 22, 80, 443 only, attached to the VM
(two layers; Docker can bypass `ufw` for published ports, and Caddy publishes none: it uses host
networking).

SSH: key login only.

```sh
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
systemctl reload ssh
```

### 2. Rootful Docker (for Caddy only)

```sh
curl -fsSL https://get.docker.com -o /tmp/get-docker.sh   # read it before running
sh /tmp/get-docker.sh
docker version
```

Nobody is added to the `docker` group.

### 3. The limited user and directories

```sh
useradd --create-home --shell /bin/bash failclosed
loginctl enable-linger failclosed            # its rootless Docker keeps running without a login
install -d -o failclosed -g failclosed -m 700 /var/lib/fail-closed /opt/fail-closed
```

### 4. cgroup v2 delegation (so sandbox limits are enforced under rootless Docker)

```sh
mkdir -p /etc/systemd/system/user@.service.d
cat > /etc/systemd/system/user@.service.d/delegate.conf <<'EOF'
[Service]
Delegate=cpu cpuset io memory pids
EOF
systemctl daemon-reload
```

Ubuntu 24.04 restricts unprivileged user namespaces with AppArmor; rootless Docker needs the
profile that Docker's setup tool prints (`/etc/apparmor.d/home.failclosed.bin.rootlesskit`).
Create it exactly as printed in step 5, then `systemctl restart apparmor`.

### 5. Rootless Docker for `failclosed` (sandboxes only)

```sh
su - failclosed
dockerd-rootless-setuptool.sh install        # prints the AppArmor profile if needed (step 4)
systemctl --user enable --now docker
echo 'export DOCKER_HOST=unix:///run/user/'"$(id -u)"'/docker.sock' >> ~/.bashrc
export DOCKER_HOST=unix:///run/user/$(id -u)/docker.sock
docker info | grep -iE 'rootless|cgroup'     # must show: rootless, cgroup v2, driver systemd
cat /sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/cgroup.controllers
#   must list: cpu cpuset io memory pids
```

### 6. Code, sandbox image, Python, web UI (as `failclosed`)

```sh
cd /opt/fail-closed
git clone https://github.com/KDiamantidis/fail-closed.git app
cd app && git checkout <release tag, e.g. v1.0>
mkdir -p data && chmod 700 data              # saved reports; systemd allows writes here only

sandbox/build.sh                             # fail-closed-sandbox:foundry-v1.8.5 in the rootless daemon

python3 -m venv backend/.venv
backend/.venv/bin/pip install -e backend

# web UI: built in a throwaway Node container, no Node on the host
docker run --rm -v "$PWD/frontend:/w" -w /w node:22-slim sh -c "npm ci && npm run build"
ls frontend/dist/index.html
```

### 7. `.env` (copied by hand, never in git)

From your machine (the file never goes through git or chat):

```sh
scp .env failclosed@<IP>:/opt/fail-closed/app/.env
```

Then on the VM, as `failclosed`, edit it and check these values:

```sh
chmod 600 /opt/fail-closed/app/.env
# required / changed for the VM:
ADMIN_TOKEN=<new random value: openssl rand -hex 32>     # never the laptop's token
GUARD_KEY_PATH=/var/lib/fail-closed/guard_ed25519.pem
HISTORY_DB=/var/lib/fail-closed/history.sqlite3
SIGNER_DB=/var/lib/fail-closed/signer.sqlite3
SANDBOX_MODE=warm
REDTEAM_PUBLIC=false                                       # public demo: show the saved report
CORS_ORIGINS=["https://<HOST>"]
VULTR_INFERENCE_API_KEY=<from the Vultr console>            # typed on the VM, never printed
```

`.env` is in `.gitignore`; `git status` on the VM must stay clean.

### 8. The API as a systemd service (host, user `failclosed`)

`/etc/systemd/system/fail-closed-api.service`:

```ini
[Unit]
Description=Fail Closed Guard API
After=network-online.target user@1001.service
Wants=network-online.target

[Service]
User=failclosed
Group=failclosed
WorkingDirectory=/opt/fail-closed/app/backend
# the user's rootless Docker (uid from `id -u failclosed`; 1001 here)
Environment=DOCKER_HOST=unix:///run/user/1001/docker.sock
Environment=XDG_RUNTIME_DIR=/run/user/1001
ExecStart=/opt/fail-closed/app/backend/.venv/bin/uvicorn app.api:app --host 127.0.0.1 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1
Restart=on-failure
RestartSec=3
# hardening: the service needs its app dir, its data dir and the Docker socket only
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/var/lib/fail-closed /opt/fail-closed/app/data
PrivateTmp=true
UMask=0077

[Install]
WantedBy=multi-user.target
```

```sh
systemctl daemon-reload && systemctl enable --now fail-closed-api
curl -s 127.0.0.1:8000/health          # {"status":"ok", ...}; the key is never in the output
journalctl -u fail-closed-api -n 50    # check: no key, no token in the log
```

One worker on purpose: demo runs, the sandbox gate and the event stream live in one process.

### 9. Caddy with HTTPS (Docker Compose, rootful Docker, no docker.sock)

`/opt/caddy/docker-compose.yml`:

```yaml
services:
  caddy:
    image: caddy:2.8          # pin the exact version
    restart: unless-stopped
    network_mode: host        # reaches 127.0.0.1:8000; publishes nothing through Docker
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    # no docker.sock, no other host paths
volumes:
  caddy_data:
  caddy_config:
```

`/opt/caddy/Caddyfile`:

```
{
	email <your email, optional>
}

<HOST> {
	encode zstd gzip
	header {
		Strict-Transport-Security "max-age=31536000"
		X-Content-Type-Options nosniff
		Referrer-Policy no-referrer
		X-Frame-Options DENY
		Content-Security-Policy "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; connect-src 'self'"
	}
	request_body {
		max_size 64KB
	}
	reverse_proxy 127.0.0.1:8000 {
		flush_interval -1     # Server-Sent Events stream without buffering
	}
}
```

```sh
cd /opt/caddy && docker compose up -d && docker compose logs -f   # wait for "certificate obtained"
curl -sI https://<HOST>/ | head -1                                 # HTTP/2 200
```

### 10. Smoke test and the containment proof (before anyone gets the link)

```sh
# as failclosed, in /opt/fail-closed/app/backend
.venv/bin/python cli.py redteam --health-url http://127.0.0.1:8000/health   # must be 7/7 contained
curl -s -X POST https://<HOST>/scenarios/d_delegation_drain/run | grep -o '"verdict":"BLOCK"' | head -1
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://<HOST>/breaker/reset-all             # 401
docker ps -a --filter label=fail-closed.sandbox                                              # only the warm pool
```

Then open `https://<HOST>/` in a browser: run replays (d) and (g), check the colour strip,
the level-4 banner, and the lift with the admin token.

## Update

```sh
# as root: back up the data first (SQLite, consistent copy)
sudo -u failclosed sqlite3 /var/lib/fail-closed/history.sqlite3 ".backup /var/lib/fail-closed/history-$(date +%F-%H%M).bak"

# as failclosed
cd /opt/fail-closed/app
git fetch --tags && git checkout <new tag>
backend/.venv/bin/pip install -e backend
docker run --rm -v "$PWD/frontend:/w" -w /w node:22-slim sh -c "npm ci && npm run build"
git diff --quiet <old tag> <new tag> -- sandbox || sandbox/build.sh      # only if the sandbox changed

# as root
systemctl restart fail-closed-api && sleep 3 && curl -s 127.0.0.1:8000/health
```

Then the smoke test from step 10. Every deploy is a git tag, so the previous version is always known.

## Roll back

```sh
# as failclosed
cd /opt/fail-closed/app && git checkout <previous tag>
backend/.venv/bin/pip install -e backend
docker run --rm -v "$PWD/frontend:/w" -w /w node:22-slim sh -c "npm ci && npm run build"
git diff --quiet <previous tag> <bad tag> -- sandbox || sandbox/build.sh
# as root
systemctl restart fail-closed-api
# only if the data was changed in an incompatible way:
systemctl stop fail-closed-api
cp /var/lib/fail-closed/history-<stamp>.bak /var/lib/fail-closed/history.sqlite3
systemctl start fail-closed-api
```

Caddy: `docker compose pull` only on purpose; to roll back, set the previous image tag and `docker compose up -d`.

## Public demo mode

What a visitor can do:

- open every page; run the incident replays (each on its own isolated state, kept 30 min);
- approve or reject held requests **inside their own replay** (never the Guard's own state);
- run the 50 + 1 simulation (mock model, labelled);
- see the containment report (the last saved run; `REDTEAM_PUBLIC=false`).

What needs the admin token (an operator, not a visitor): lifting a global stop or any breaker
reset, approving or rejecting the Guard's own held requests, a live red-team run. The token is
typed into the page, sent once in a header and never stored.

**Rate limits, so strangers cannot burn the Vultr credit.** Every replay asks the model for
explanations (e.g. replay (e) has 21 steps). Before the link is public, add (not built yet;
proposed values):

| Limit | Where | Proposed value |
|---|---|---|
| Replays per IP | API | 10 per 10 minutes, then 429 "try again later" |
| Simulation runs | API (already one at a time) | 2 per IP per hour |
| `/check` and `/withdraw` (the Guard's own state) | API | admin token in public mode, or 30 per IP per hour |
| Model budget per day | API, counted from token usage | e.g. $1/day; above it the AI analysis says "demo budget reached", the verdict is unchanged, **no silent switch to the mock** |
| Model calls at once | already | 3 (`LLM_CONCURRENCY`) |
| Request size | Caddy | 64 KB (above) |
| Concurrent demo runs kept | API (already bounded) | unchanged |

The per-IP limits read the client IP from Caddy's `X-Forwarded-For` (uvicorn trusts only
127.0.0.1). Also set a usage alert for the inference subscription in the Vultr console, if it offers one.

## What I need from you

1. The VM's **public IPv4** (and IPv6 if it has one), once it is running.
2. **Who runs the commands**: you over SSH (I give you each step), or me, with an SSH key you
   authorise for a non-root sudo user. Either way, no password login.
3. The **domain**: `<ip>.sslip.io` is fine, or a domain you own (then one A record to the IP).
4. An **email for the certificate** (optional; for expiry notices).
5. A **Vultr firewall group** with inbound 22, 80, 443 only, attached to the VM (console).
6. On the VM, typed by you: the **Vultr inference key** in `.env`, and a **new `ADMIN_TOKEN`**
   (`openssl rand -hex 32`). Neither is ever sent to me or put in chat.
7. Decisions: the **daily model budget** for the public demo, whether `/check` and `/withdraw`
   are admin-only in public mode, and a **release tag** to deploy (e.g. `v1.0`).
8. If rootless Docker will not enforce the sandbox limits on this kernel (red team not 7/7):
   whether to fall back to the rootful daemon for sandboxes with the API user in the `docker`
   group (simpler, but that group is root-equivalent; the README says so).
