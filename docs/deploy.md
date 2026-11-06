# Deploy: Vultr VM (ready, not deployed)

Target: **Ubuntu 24.04, 2 vCPU / 4 GB, Stockholm**, SSH key login, user `root` at first.
Domain: **`<ip>.sslip.io`** (resolves to the VM, free, real certificate; no certificate email).
Release: git tag **`v1.0-hackathon`**.

Everything runs from this laptop with `deploy/deploy.sh` over SSH (key `~/.ssh/id_ed25519`).
The scripts in `deploy/remote/` run on the VM. **Secrets never pass through the scripts or
their output**: you type the Vultr key and the admin token on the VM yourself.

## Layout

```
Internet ──443/80──► Caddy (Docker Compose, system Docker, host network, no docker.sock)
                        ▼  HTTPS
                     127.0.0.1:8000  Guard API (systemd, user "failclosed", on the HOST)
                        ▼  Docker SDK, failclosed's OWN rootless Docker daemon
                     sandbox containers (no network, read-only, 512 MB, 1 CPU, 128 pids, no caps)
```

- **No `docker.sock` in any container.** Caddy only proxies HTTP.
- **Two Docker daemons.** The system daemon runs only Caddy. The `failclosed` user's rootless
  daemon runs only sandboxes, so Docker access is not root on the host.
- **Sandbox limits unchanged.** Rootless Docker enforces memory / CPU / pids only with cgroup v2
  delegation; `install` sets it up and stops if `cpu`, `memory` or `pids` is not delegated, and
  `verify` runs the red team. **No link goes out unless the red team is 7/7 contained.**
- **Fallback (allowed):** if the red team is not 7/7 under rootless Docker, `install --docker
  rootful` puts the sandboxes on the system daemon and `failclosed` in the `docker` group, which
  is root-equivalent. If we use it, README "Honest limits" says so.
- Users: `ops` (your key, sudo) for administration; `failclosed` (no login, no sudo) runs the API.
  Root SSH login and password login are turned off by `bootstrap`.
- Firewall: `ufw` allows 22, 80, 443 only. Caddy publishes no Docker ports (host network), so
  Docker cannot open ports behind `ufw`.
- Public demo mode on: `PUBLIC_DEMO=true`, `LLM_DAILY_BUDGET_USD=1` (kept across restarts),
  `REDTEAM_PUBLIC=false`, `LLM_MODE=live` (no key = AI off, never the mock).

## Deploy day, step by step

| # | Who | What |
|---|---|---|
| 1 | you | Tell Claude the VM's IPv4 address. |
| 2 | Claude | `deploy/deploy.sh <IP> bootstrap`: user `ops` with your key, firewall, swap, Docker; then root and password login off (checked: `ops` works first, root is refused after). |
| 3 | Claude | `deploy/deploy.sh <IP> install`: user `failclosed`, rootless Docker, code at `v1.0-hackathon`, sandbox image, web UI, `.env` with both secrets **empty**, systemd unit, Caddy files. About 10 minutes. |
| 4 | Claude | `deploy/deploy.sh <IP> secrets`: prints the exact command for you. |
| 5 | **you** | In **your own terminal** (not the Claude Code prompt, not with `!`, so nothing reaches the chat): `ssh -t -i ~/.ssh/id_ed25519 ops@<IP> "sudo -u failclosed nano /opt/fail-closed/app/.env"`. Fill in `VULTR_INFERENCE_API_KEY=` and `ADMIN_TOKEN=` (make one with `ssh -i ~/.ssh/id_ed25519 ops@<IP> "openssl rand -hex 32"`). Save: Ctrl+O, Enter, Ctrl+X. Keep the token safe. Tell Claude "secrets done". |
| 6 | Claude | `deploy/deploy.sh <IP> finish`: checks both lines are filled (prints only "set" / "MISSING"), starts the API and Caddy, waits for the certificate. |
| 7 | Claude | `deploy/deploy.sh <IP> verify`: health, red team 7/7, leftover sandboxes, **key and token in no log** (counts only), then over HTTPS: certificate, replay (d) BLOCK, replay (g) reaches level 4, raw `/check` refused (401). |
| 8 | you | Open `https://<IP>.sslip.io`, run (d) and (g), try "Reset demo" and the admin lift with your token. |
| 9 | Claude | If step 7's red team is not 7/7: `deploy/deploy.sh <IP> install --docker rootful`, then `finish` and `verify` again, and add the line to README "Honest limits". |

`deploy/deploy.sh <IP> all` runs 2–7 in one go and waits for Enter at step 5.

On the first SSH connection the host key is accepted and remembered (`accept-new`). If you want
to compare it, the Vultr console shows the VM's SSH host key fingerprints after boot.

## Update

```sh
git tag v1.0.1-hackathon && git push origin v1.0.1-hackathon     # on the laptop
deploy/deploy.sh <IP> release v1.0.1-hackathon
```

`release` (`deploy/remote/release.sh`) on the VM:
1. backs up `history.sqlite3` and `signer.sqlite3` with SQLite `.backup` into
   `/var/lib/fail-closed/backups/<time>-<old tag>/`;
2. checks out the new tag, `pip install`, builds the web UI;
3. rebuilds the sandbox image only if `sandbox/` changed between the two tags;
4. restarts the API and waits for `/health`; **if it is not healthy, it rolls back to the old tag
   on its own**;
5. records the tag in `/var/lib/fail-closed/deployed_tag`.

Caddy changes are separate: copy `deploy/Caddyfile` and `cd /opt/caddy && sudo docker compose up -d`.

## Roll back

```sh
deploy/deploy.sh <IP> release v1.0-hackathon        # any earlier tag
```

Only if the data must go back too (an incompatible change), on the VM as `ops`:

```sh
sudo systemctl stop fail-closed-api
ls /var/lib/fail-closed/backups/                     # pick the backup taken before the bad release
sudo -u failclosed cp /var/lib/fail-closed/backups/<time>-<tag>/*.sqlite3 /var/lib/fail-closed/
sudo systemctl start fail-closed-api
```

## Public demo mode (`PUBLIC_DEMO=true`)

Visitors can:
- run the incident replays and the 50 + 1 simulation;
- use the review queue demo (approve / reject inside their own replay);
- read the policies and the containment report (the last saved red-team run);
- press **Reset demo**: their own demo data (breaker, queue, history) back to level 0.

Admin only (`ADMIN_TOKEN`, header `X-Admin-Token`):
- raw `/check` and `/withdraw` on the Guard's own state;
- lifting a real breaker level (`/breaker/reset`, `/breaker/reset-all`, and the "Lift global
  stop" button, which stays in the UI as the real action);
- the Guard's own held requests (`/pending/...`);
- a live red-team run.

Policies cannot be changed through the API at all: they are read-only (`GET /policies`).

Built in:

| Protection | Value |
|---|---|
| Replays per IP | 10 per minute |
| Simulations per IP | 2 per 10 minutes (and one at a time overall) |
| Demo actions per IP (review decisions, Reset demo) | 30 per minute |
| When a limit is hit | 429 with a plain message, e.g. "Too many replays from your address: at most 10 per minute. Please try again in 42 s." |
| Idle auto-reset | a visitor's replays are dropped after 10 minutes without activity; the page resets to level 0 too |
| Daily model budget | $1/day (UTC), kept across restarts; then "Demo budget reached: AI explanations paused until tomorrow"; decisions keep working (the AI check counts as unavailable, so a full simulation runs); never the mock |
| Model calls at once | 3, of which explanations use at most 2 |
| Request size | 64 KB (Caddy) |

Each replay runs on its own isolated state, so one visitor cannot push the breaker up for
anyone else; the only shared state is the Guard's own, and in public mode only the admin can
change it. The client IP for the limits is the last `X-Forwarded-For` entry, trusted only from
127.0.0.1 (Caddy or the tunnel on the same host).

## Plan B: no VM by Saturday (laptop + free HTTPS tunnel)

Steps only; **not started**. Uses a Cloudflare "quick tunnel": no account, a random
`https://<words>.trycloudflare.com` address, HTTPS by Cloudflare. No ports are opened on the laptop.

1. Laptop: plugged in, sleep off while the demo runs (Windows: Settings → Power → Never sleep).
2. Docker Desktop running; sandbox image built (`sandbox\build.ps1`); web UI built
   (`cd frontend; npm run build`).
3. In `.env` (you edit it; secrets as today): `PUBLIC_DEMO=true`, `LLM_MODE=live`,
   `LLM_DAILY_BUDGET_USD=1`, `LLM_BUDGET_FILE=data/llm_budget.json`, `REDTEAM_PUBLIC=false`,
   `SANDBOX_MODE=warm`, and a **new** `ADMIN_TOKEN`.
4. Check free RAM first (≥ 2 GB, as for the load test), then start the API on localhost only:
   `cd backend; .venv\Scripts\python -m uvicorn app.api:app --host 127.0.0.1 --port 8000 --no-proxy-headers`
5. Install the tunnel once: `winget install --id Cloudflare.cloudflared`.
6. Start it: `cloudflared tunnel --url http://127.0.0.1:8000`. It prints the public HTTPS address.
7. Add that address to `CORS_ORIGINS` in `.env` and restart the API (step 4).
8. Check from a phone on mobile data: the page loads, replay (d) is blocked, `/check` gives 401.
9. Stop: Ctrl+C in both windows. The address dies with the tunnel.

Limits of plan B: the address changes every time the tunnel restarts; the laptop must stay on;
Docker Desktop on Windows is not the rootless setup (say so in the README if we use it); a quick
tunnel has no uptime guarantee (a free named tunnel needs a Cloudflare account and a domain).

## What I need from you on deploy day

1. The VM's **IPv4** (and that it is the Ubuntu 24.04 image with your key `~/.ssh/id_ed25519` for root).
2. Five minutes at step 5 to type the **Vultr key** and a **new admin token** on the VM in your own terminal.
3. A browser check at step 8.
4. Only if the red team fails under rootless Docker: OK to use the rootful fallback (you already
   allowed it; I will note it in the README).
