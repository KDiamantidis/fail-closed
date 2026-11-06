# Submission checklist (lablab.ai)

## Fields

| Field | Value | Status |
|---|---|---|
| Project title | Fail Closed | ready |
| Short description | `docs/submission/lablab.md`, "Short description" (115 characters) | ready |
| Long description | `docs/submission/lablab.md`, "Long description": pick the VM or the Plan B lines | pick one version |
| Cover image | `docs/brand/cover-1920x1080.png` (also `cover-1280x720.png`) | ready |
| Video | record with `video-script.md` and `shot-list.md`, under 5 minutes; upload (e.g. YouTube, unlisted) and paste the link | to do |
| Slides PDF | `docs/slides/fail-closed-vm.pdf`, or `docs/slides/fail-closed-laptop.pdf` for Plan B | pick one |
| GitHub URL | https://github.com/KDiamantidis/fail-closed | ready (public) |
| App URL | the VM: `https://<ip>.sslip.io` · Plan B: the `https://….trycloudflare.com` address (changes on every restart) | after deploy |
| Technologies | `lablab.md`, "Technologies" | ready |
| Tags | `lablab.md`, "Tags" | ready |
| Release tag | `v1.2-hackathon` | ready |

Also switch the README to the version you use:
- **VM worked:** keep the README as it is (the "vm" text) and add the live link.
- **Plan B:** in README "Live demo", use the Plan B sentence from `lablab.md`
  ("The Vultr VM was blocked by account review …") and the tunnel address.

## Final checks

| Check | How | Result (2026-11-06) |
|---|---|---|
| README images load on GitHub | every local link in README points to a tracked file | all 8 local links exist and are tracked; open the repo page once to see the cover and the diagram |
| Repo is public | `gh repo view KDiamantidis/fail-closed --json visibility` | PUBLIC |
| LICENSE present | `LICENSE` | MIT, KDiamantidis |
| No secrets in the history | the Vultr key from `.env` searched in `git log --all -p`; patterns (private keys, API key formats); `.env`, `*.pem`, `*.key` ever committed | key 0 hits; patterns 0; `.env` never committed; no key files |
| Git author on all commits | `git log --all --format='%an <%ae>'` and committers | 86 of 86 commits by KDiamantidis &lt;kociama@cs.duth.gr&gt; (author and committer) |
| Tests | Python 3.12 container; host; Docker + browser | 575 passed, 28 skipped · 574 passed, 1 skipped · 28 passed, 0 containers left |
| Name check | `backend/tests/test_no_real_names.py` | passes: no real company names on the website |

Re-run the secret and author checks right before submitting if you commit anything else.

## On the day

- [ ] VM or Plan B decided by 10:00
- [ ] `ADMIN_TOKEN` set by you (never shared)
- [ ] Containment report saved (Plan B: `.\deploy\planb.ps1 redteam`; VM: `deploy.sh <IP> verify`)
- [ ] Phone test on mobile data, incl. 11 replays → "Too many replays"
- [ ] Reset demo before recording
- [ ] Video recorded and uploaded
- [ ] README "Live demo" line updated with the real address
- [ ] Form submitted; copy of the texts kept
