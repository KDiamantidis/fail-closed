# lablab.ai submission text

Copy each part into the matching field. Facts about the real incidents come only from the README
"Motivation" section and its sources. Measured numbers say where they were measured.

## Project title

Fail Closed

## Short description (max ~150 characters)

An independent co-signer for crypto wallets: unusual withdrawals run in an isolated sandbox before anything is signed.

(118 characters)

## Long description (about 600 words with your "where it runs" line)

**The problem.** In October and November 2026, two exchanges lost funds through signatures that
were valid but should never have been made. At Binance, about $6.3 billion left hot wallets on
about 10 blockchains. One Ethereum transaction carried 50 EIP-7702 authorizations for 50
consecutive nonces; one matched and handed a hot wallet to the attacker's contract (Blockaid).
Binance says it found no evidence of a private key leak; the investigation is ongoing. At
Mercado Bitcoin, about $38–49M (estimates vary) left through plain transfers to attacker
addresses on at least 10 chains. Both exchanges then paused withdrawals for all customers.
Fail Closed is designed to reject this class of attack. We do not claim it would have stopped
these incidents.

**The idea.** No approval, no signature. Fail Closed sits between the withdrawal service and the
signer. The signer signs only with a Guard approval bound to the exact request. The Guard checks
what a signature will do, not who asks for it.

**How it works.** Fixed rules check every request. A plain payout to a customer this wallet has
paid before, of at most 10% of the balance, is approved by the rules alone, in milliseconds. Above
10%, a quick AI check comes first (glm-5.3-flash, 2-second limit); if the AI wants a closer look,
or does not answer in time, the request goes to the sandbox. Everything else goes to the sandbox
first: a new destination, an EIP-7702 delegation, a contract call, or anything a rule flags (for
example more than 25% of the balance). The sandbox is a local copy of the chain (Docker and
Anvil): no network, read-only, 512 MB, 1 CPU, no secrets. The rules read what really changes and
decide: allow, delay, needs review or block. Any sandbox error or timeout means block. A frozen address or
a paused wallet is blocked at once. A scoped circuit breaker stops as little as possible: one
address, wallet or chain; everything only with two independent signals. The AI explains each
decision. It never decides.

For Challenge 1, the Guard is the control layer: it plans the check, dispatches the transaction into
an isolated sandbox, and verifies the real executed output with fixed rules. Each sandbox is
isolated, holds no secrets, has hard resource limits and is destroyed after the check.

**What runs on Vultr.** Vultr Serverless Inference runs the AI: glm-5.3-flash for the fast check
of large amounts, glm-5.3 for the explanation card. [WHERE: choose one line below.]

**Measured results.** On a laptop (Windows 11, Docker Desktop, 6 Nov 2026), with 500 normal
withdrawals and 1 hidden attack: the attack was stopped in all 4 setups (a fresh sandbox per check
or a warm pool of reused sandboxes, each with the AI check on and off) and 0 of 500 normal
customers were refused. Fast path median 3 ms (warm setup); full sandbox check median 1.3 s (warm)
or 2.4 s (fresh). A short run with the real Vultr models (49 + 1) stopped the attack with 0 refused
and 0 rate limits. The red team ran hostile code inside the sandbox: 7 of 7 contained. In our model
comparison, glm-5.3-flash answered 24 of 24 checks within 2 s.

**Where it fits.** Pre-signing screening products and signer hooks already exist. The hooks give
the enforcement point; the customer writes the decision logic. Fail Closed is a prototype of that
decision logic.

**Honest limits.** A hackathon prototype on a local test chain with fake funds. It does not stop
the first break-in; it stops that break-in from getting a signature. If an attacker takes over the
Guard or its key, the protection is gone. [LIMITS: choose one line below.]

**What's next.** Plug into one real signer hook; check every signature against an approved
withdrawal in the exchange ledger; deny unknown operation types (for example EIP-7702 delegation)
on withdrawal wallets; rules for deposit-address sweeps.

### Where it runs: pick ONE

- **[WHERE] if the VM worked:** Vultr Cloud Compute runs the Guard, the sandboxes and the web app on one VM.
- **[WHERE] if Plan B:** The Vultr VM was blocked by account review before the deadline, so the demo runs on the laptop; the AI still runs on Vultr Serverless Inference.
- **[LIMITS] if the VM worked:** All measured numbers above come from a laptop; the VM runs the live demo.
- **[LIMITS] if Plan B:** The Vultr VM was blocked by account review, so the demo and all numbers are from a laptop.

## Technologies: pick ONE

- **If the VM worked:** Python, FastAPI, Pydantic, Docker (rootless), Foundry Anvil (Prague
  hardfork), EIP-7702, SQLite, ed25519 approvals, React, Vite, Tailwind CSS, Server-Sent Events
  with polling fallback, Playwright, Caddy, Vultr Serverless Inference (glm-5.3-flash, glm-5.3),
  Vultr Cloud Compute.
- **If Plan B:** Python, FastAPI, Pydantic, Docker Desktop, Foundry Anvil (Prague hardfork),
  EIP-7702, SQLite, ed25519 approvals, React, Vite, Tailwind CSS, Server-Sent Events with polling
  fallback, Playwright, Cloudflare Tunnel, Vultr Serverless Inference (glm-5.3-flash, glm-5.3).

## Tags

AI agents, security, crypto, wallets, EIP-7702, sandbox, Vultr, Blast Radius Zero
