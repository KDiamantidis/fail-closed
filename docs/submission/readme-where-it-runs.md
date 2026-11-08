# README: "where it runs" (VM)

The README is on the **VM** version. The live demo runs on the Vultr VM. The three relevant places:

## 1. "Live demo" line (top of the README)

- `**Live demo:** https://70.34.205.46.sslip.io`

## 2. "Vultr services used" table, Cloud Compute row

- `| **Vultr Cloud Compute** | One VM runs everything else: Caddy (HTTPS), the Guard API on the host as a limited user, the sandboxes (Docker + Anvil, rootless Docker), SQLite history and the web UI. Plan: docs/deploy.md. |`

## 3. "Honest limits", the line about measured numbers

- `- All measured numbers come from a laptop (6–7 Nov 2026).`

The web page footer follows `DEPLOY_TARGET` (`vm`) on its own. Slides: `docs/slides/fail-closed-vm.pdf`.
