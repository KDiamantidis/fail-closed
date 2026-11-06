# Deploy: Vultr VM (ready, not deployed)

Target: **Ubuntu 24.04, 2 vCPU / 4 GB, Stockholm**, SSH key login, user `root` at first.
Domain: **`<ip>.sslip.io`** (resolves to the VM, free, real certificate; no certificate email).
Release: git tag **`v1.1-hackathon`** (`v1.0-hackathon` is the earlier one, kept).

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
| 3 | Claude | `deploy/deploy.sh <IP> install`: user `failclosed`, rootless Docker, code at `v1.1-hackathon`, sandbox image, web UI, `.env` with both secrets **empty**, systemd unit, Caddy files. About 10 minutes. |
| 4 | Claude | `deploy/deploy.sh <IP> secrets`: prints the exact command for you. |
| 5 | **you** | In **your own terminal** (not the Claude Code prompt, not with `!`, so nothing reaches the chat): `ssh -t ops@<IP> "sudo -u failclosed nano /opt/fail-closed/app/.env"`. Fill in `VULTR_INFERENCE_API_KEY=` and `ADMIN_TOKEN=` (make one with `ssh ops@<IP> "openssl rand -hex 32"`). Save: Ctrl+O, Enter, Ctrl+X. Keep the token safe. Tell Claude "secrets done". |
| 6 | Claude | `deploy/deploy.sh <IP> finish`: checks both lines are filled (prints only "set" / "MISSING"), starts the API and Caddy, waits for the certificate. |
| 7 | Claude | `deploy/deploy.sh <IP> verify`: health, red team 7/7, leftover sandboxes, **key and token in no log** (counts only), then over HTTPS: certificate, replay (d) BLOCK, replay (g) reaches level 4, raw `/check` refused (401). |
| 8 | you | Open `https://<IP>.sslip.io`, run (d) and (g), try "Reset demo" and the admin lift with your token. |
| 9 | Claude | If step 7's red team is not 7/7: `deploy/deploy.sh <IP> install --docker rootful`, then `finish` and `verify` again, and add the line to README "Honest limits". |

`deploy/deploy.sh <IP> all` runs 2–7 in one go and waits for Enter at step 5.

On the first SSH connection the host key is accepted and remembered (`accept-new`). If you want
to compare it, the Vultr console shows the VM's SSH host key fingerprints after boot.

## Update

```sh
git tag v1.2-hackathon && git push origin v1.2-hackathon     # on the laptop
deploy/deploy.sh <IP> release v1.2-hackathon
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

## Plan B: no VM by Saturday 10:00 (laptop + Cloudflare tunnel)

For the submission, if we use it:

> The Vultr VM was blocked by account review before the deadline, so the demo runs on the
> laptop; the AI still runs on Vultr Serverless Inference.

### 1. Start the app on the laptop (local only)

`deploy/planb.ps1` runs the app like the VM does: `PUBLIC_DEMO=true`, the same per-IP limits
(10 replays a minute, 2 simulations per 10 minutes, 30 demo actions a minute), a $1/day AI budget
kept in `data/planb/llm_budget.json`, the AI on Vultr Serverless Inference (`LLM_MODE=live`: no
key means AI off, never the mock), the warm sandbox pool. No Caddy: the tunnel gives HTTPS. The
API listens on 127.0.0.1:8000 only. Secrets stay in the local `.env`; the script only checks
that they are set and never prints them.

```powershell
.\deploy\planb.ps1 start     # checks free RAM (>= 2 GB), Docker, the image, the built UI, the secrets
.\deploy\planb.ps1 status    # API, Plan B sandbox count, free RAM, today's AI spend
.\deploy\planb.ps1 stop      # stops the API and removes every Plan B sandbox container
```

Before the first `start`, set `ADMIN_TOKEN` yourself (the script refuses to start without it):
`backend\.venv\Scripts\python -c "import secrets; print(secrets.token_hex(32))"`, then
`notepad .env` and paste it into the `ADMIN_TOKEN=` line. (`start -Rehearsal` runs without it,
with every admin action disabled: for local tests only.)

Rehearsal on 2026-11-06, this laptop (Windows 11, 16 CPUs, Docker Desktop), `start -Rehearsal`:
replay (d) blocked in 2.9 s, nothing signed, explanation by `glm-5.3`; replay (g) reached the
global stop in 14.2 s; Reset demo back to level 0; `stop` removed all 4 sandboxes, none left; AI
spend $0.004; the key in no log file. Free RAM went from 3.9 GB down to 1.1 GB at the lowest
(API + 4 warm sandboxes + the test browser): close other programs before the demo.

### 2. Install cloudflared (once, by hand)

```powershell
winget install --id Cloudflare.cloudflared
cloudflared --version            # open a new terminal first if it is not found
```

(Or download `cloudflared-windows-amd64.msi` from github.com/cloudflare/cloudflared/releases.)

### 3a. Quick tunnel: no account, no domain

```powershell
cloudflared tunnel --url http://127.0.0.1:8000
```

It prints a public `https://<random-words>.trycloudflare.com` address. Limits, from Cloudflare's
documentation (developers.cloudflare.com, "Quick Tunnels"):
- **No Server-Sent Events.** Our UI gets the live replay steps (the rising risk colour) and the
  AI explanations over SSE. Through a quick tunnel, replays still finish and show their verdicts,
  but the live steps and the explanation cards would not arrive. Test it before sharing the link.
- At most 200 requests in flight at once (more get 429).
- **The address changes every time** cloudflared starts, and stops working when it stops.
- **The laptop must stay on and awake**, with Docker Desktop running (Settings → Power: never sleep).

### 3b. Named tunnel: a stable address, SSE works

Needs a free Cloudflare account and **a domain whose DNS is on Cloudflare** (nameservers pointed
to Cloudflare). Then, once:

```powershell
cloudflared tunnel login                                   # opens the browser, pick the domain
cloudflared tunnel create fail-closed
cloudflared tunnel route dns fail-closed demo.<your-domain>
```

and a `%USERPROFILE%\.cloudflared\config.yml`:

```yaml
tunnel: fail-closed
credentials-file: C:\Users\<you>\.cloudflared\<tunnel-id>.json
ingress:
  - hostname: demo.<your-domain>
    service: http://127.0.0.1:8000
  - service: http_status:404
```

Run it with `cloudflared tunnel run fail-closed`. The address stays the same across restarts.
The laptop must still stay on and awake.

### 4. Before sharing the link

- From a phone on mobile data: the page loads, replay (d) is blocked, `POST /check` answers 401.
- The rate limits see the visitor's address: the API trusts `X-Forwarded-For` only from
  127.0.0.1, which is where cloudflared connects from. Run 11 replays in a minute from one phone:
  the 11th must get "Too many replays".
- `.\deploy\planb.ps1 status` every so often: sandbox count, free RAM, AI spend.
- Afterwards: Ctrl+C in the cloudflared window, then `.\deploy\planb.ps1 stop`.

## What I need from you on deploy day

1. The VM's **IPv4** (and that it is the Ubuntu 24.04 image with your key `~/.ssh/id_ed25519` for root).
2. Five minutes at step 5 to type the **Vultr key** and a **new admin token** on the VM in your own terminal.
3. A browser check at step 8.
4. Only if the red team fails under rootless Docker: OK to use the rootful fallback (you already
   allowed it; I will note it in the README).
