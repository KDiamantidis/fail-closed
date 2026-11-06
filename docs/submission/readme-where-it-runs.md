# README: the two "where it runs" versions

The README is on the **VM** version now. If Plan B is used, replace these three places with the
laptop version.

## 1. "Live demo" line (top of the README)

- **VM:** `**Live demo:** https://<ip>.sslip.io`
- **Laptop (Plan B):** `**Live demo:** https://<words>.trycloudflare.com (runs on the demo laptop: the Vultr VM was blocked by account review before the deadline; the AI still runs on Vultr Serverless Inference)`

## 2. "Vultr services used" table, Cloud Compute row

- **VM:**
  `| **Vultr Cloud Compute** | One VM runs everything else: Caddy (HTTPS), the Guard API on the host as a limited user, the sandboxes (Docker + Anvil, rootless Docker), SQLite history and the web UI. Plan: docs/deploy.md. |`
- **Laptop (Plan B):**
  `| **Vultr Cloud Compute** | Planned for the Guard, the sandboxes and the web UI (docs/deploy.md), but the VM was blocked by account review before the deadline. The demo runs on a laptop behind a Cloudflare tunnel; the AI still runs on Vultr Serverless Inference. |`

## 3. "Honest limits", the line about measured numbers

- **VM:** `- All measured numbers come from a laptop, not yet from the Vultr VM.`
- **Laptop (Plan B):** `- The Vultr VM was blocked by account review, so the demo and all measured numbers are from a laptop (Docker Desktop, not rootless Docker).`

The web page footer follows `DEPLOY_TARGET` (`vm` or `laptop`) on its own, and the slides come in
both versions (`docs/slides/fail-closed-vm.pdf`, `fail-closed-laptop.pdf`).
