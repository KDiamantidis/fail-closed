# Fail Closed

**An independent co-signer for crypto wallets.**
Before anything is signed, an AI agent runs the request in an isolated sandbox on Vultr, checks what it really does, and blocks it if it is dangerous.

> No approval, no signature. If we are not sure, the answer is no.

Built for the **Vultr Agent Rush** hackathon on lablab.ai, **Challenge 1: Blast Radius Zero** (3–8 November 2026).

![Architecture](docs/architecture.png)

---

## The problem

On **29 October 2026**, about **$6.3 billion** left Binance wallets on around 10 blockchains, most of it in about 20 minutes. It is the largest crypto theft on record.

What we know so far (Binance has not published a full root cause yet):

- A DevOps engineer's laptop was compromised. From there, the attacker reached a service that submits withdrawals. *(Binance)*
- **No private keys were leaked.** The signing system signed what it was asked to sign. *(Binance, TRM Labs)*
- On Ethereum, one transaction carried **50 EIP-7702 authorizations** for a hot wallet, one for each of 50 consecutive nonces. One matched, and the wallet started running an attacker "sweeper" contract that only the attacker could call. *(on-chain)*
- On the other chains, the funds left through **plain signed transfers** to unknown addresses. *(on-chain)*
- The delegation stayed active, and automatic top-ups kept refilling the hijacked wallet for hours. *(on-chain)*

The common problem on every chain: **the signer trusted where a request came from, and never checked what it would do.**

## The idea

Fail Closed sits **between the request and the signer**. The signer will not sign without a signed approval from Fail Closed, bound to the exact request.

Fail Closed:

1. **Judges the effect, not the source.** A request from an "internal" service gets the same checks as any other.
2. **Runs unusual requests for real** in a fresh, locked sandbox (local blockchain, no internet, no secrets) and looks at what changes. The sandbox's starting state (balances, nonces, code) comes from the Guard's own side, never from the request.
3. **Lets deterministic rules decide.** The AI explains the decision in plain words and gives a risk score. It may ask for a deeper check (fast path → slow path), but it never decides: it can never allow, never downgrade, never turn a BLOCK into an ALLOW. If the AI fails, the verdict is the same, just without an explanation.
4. **Fails closed.** If the sandbox breaks, times out or returns nothing, or a rule crashes, the answer is BLOCK.

## How it works

### Two paths

| Path | When | What happens | Speed |
|---|---|---|---|
| **Fast path** | Plain transfer, known destination, small amount, wallet under its limits | Approve right away, no sandbox | *(to be measured)* |
| **Slow path** | Anything else: EIP-7702, contract calls, unknown destination, large amount | Run in the sandbox, read the state diff, apply rules | *(to be measured)* |

The fast path does not ask "does this look bad?". It asks "does this match something we know is safe, exactly?". Everything else gets checked.

On the fast path the AI may only say "escalate to the slow path" or "no change". An AI error or timeout also sends the request to the slow path: more checking, never less. (Switch: `FAST_PATH_LLM_ESCALATION`, on by default, so we can measure with and without it.)

### Two kinds of wallets

Every request names its `wallet_type` and `chain_id`.

| Wallet type | Sends to | Rules |
|---|---|---|
| `customer_hot` | Customers' own addresses, unknown in advance. No allowlist. | Behavior rules: R3, R6, R7, R8 |
| `treasury` | A few known addresses | Strict allowlist: R4, R5 |

R0, R1 and R2 apply to every wallet.

### Verdicts

`ALLOW` → `DELAY` → `NEEDS_HUMAN` → `BLOCK`

- **DELAY**: hold for 10–15 minutes, then release automatically if no other signal appears.
- **Final verdict**: any BLOCK → **BLOCK**; else any NEEDS_HUMAN → **NEEDS_HUMAN**; else any DELAY → **DELAY**; else **ALLOW**.

### Rules

| ID | Rule | Wallets | Verdict |
|---|---|---|---|
| R0 | Sandbox, trace, diff or receipt failed or empty; or a rule crashed or missed an input | all | BLOCK |
| R1 | An account gets delegation code (`0xef0100…`) to a contract not on the allowlist | all | BLOCK |
| R2 | More than one authorization in a request, or authorizations for consecutive nonces | all | BLOCK |
| R3 | A wallet loses more than X% of its balance to one non-allowlisted address | customer_hot | BLOCK |
| R4 | Amount above limit | treasury | NEEDS_HUMAN |
| R5 | Destination not on the allowlist | treasury | NEEDS_HUMAN (BLOCK if also above limit) |
| R6 | Wallet outflow in a time window above limit | customer_hot | BLOCK |
| R7 | Same destination receives from ≥ 3 chains in 6 h | customer_hot | DELAY; from ≥ 5 chains → freeze that address |
| R8 | ≥ 10 different assets to one address in 30 min | customer_hot | Freeze that address |
| C1 *(planned)* | A transaction that is not a native transfer or an ERC-20 `transfer(address,uint256)` to a known token | customer_hot | NEEDS_HUMAN |

Thresholds are starting values in `.env`, not tuned on real traffic.

### Circuit breaker

Steps, from narrow to wide. Each step is **latched**: it stays on until a human resets it.

| Level | Action | Trigger |
|---|---|---|
| 1 | Freeze one destination address | e.g. R7 (≥ 5 chains), R8 |
| 2 | Pause one wallet | Repeated blocks on one wallet |
| 3 | Safe mode on one chain | Many signals on one chain |
| 4 | Global stop | Only with **≥ 2 independent signals** |

A full stop hurts every customer, so the breaker starts as narrow as possible.

### Example: a Binance-style request

1. A compromised service asks to sign a transaction with 50 EIP-7702 authorizations for a hot wallet.
2. The signer forwards it to Fail Closed. It cannot sign without approval.
3. Fail Closed runs it in a fresh sandbox with a copy of the wallet (fake funds).
4. The state diff shows the wallet now runs unknown code, and a test sweep empties it.
5. R1, R2 and R3 fire → **BLOCK**. The AI writes: *"This gives full control of the wallet to an unknown contract."*
6. No approval → **no signature**. The sandbox is destroyed.

## Sandbox (containment)

Every slow-path check runs in a new Docker container that is destroyed afterwards:

- no network (`--network none`)
- read-only filesystem, temporary files in memory only
- memory, CPU, process and time limits
- all Linux capabilities dropped, no privilege escalation
- **no keys or secrets inside, ever**

The demo has a **red-team button** that sends hostile code straight into the sandbox (tries to reach the internet, read secrets, write files, fork-bomb). Every attempt fails and is shown in the logs.

## Vultr services used

| Service | Use |
|---|---|
| **Vultr Cloud Compute** | Runs the Guard API, the sandboxes and the web app |
| **Vultr Serverless Inference** | The AI that writes the intent summary, risk score and explanation |
| *(to be updated)* | |

## Tech stack

Python + FastAPI · Docker · Foundry (Anvil, Prague hardfork) · Vultr Serverless Inference (OpenAI-compatible API) · SQLite · React + Vite + Tailwind · Server-Sent Events · Docker Compose + Caddy

## Results

*(Measured numbers will go here: fast path time, slow path time, 500 normal + 1 attack test, red-team results.)*

## Honest limits

- If an attacker also takes over Fail Closed or its approval key, the protection is gone. It must run on separate infrastructure with separate access.
- Small transfers to addresses already on the allowlist may pass. Time limits reduce the damage but do not remove it.
- Fail Closed does not stop the first break-in (for example a stolen laptop). It stops that break-in from getting a signature.
- This is a hackathon prototype. The demo uses a local test chain with fake funds, not mainnet.
- The API runs on the host and needs Docker access, which is root-equivalent with normal Docker. Production setup: rootless Docker for the API user with cgroup v2 delegation, otherwise sandbox memory / CPU / pids limits are not enforced. Red-team fork-bomb and memory tests must prove the limits hold.
- Unsigned EIP-7702 authorizations can only be simulated for sandbox test accounts (they are signed inside the sandbox with the public Anvil test keys). Pre-signed authorizations are not supported yet; both cases fail closed (BLOCK).

## How to run

Needs Docker (Docker Desktop on Windows) and Python 3.12+.

```sh
# 1. Build the sandbox image (Foundry v1.8.5, contracts compiled at build time)
sandbox/build.sh                # Windows: .\sandbox\build.ps1

# 2. Run the Binance-style scenario end to end
scenarios/run.sh 7702_attack    # Windows: .\scenarios\run.ps1 7702_attack
```

The scenario prints the state diff (including the `0xef0100…` delegation code), the
balance changes, the authorizationList, the AI result, the time of each step and the
container limits as Docker applied them.

AI: copy `.env.example` to `.env` and set `VULTR_INFERENCE_API_KEY`. Without a key the
AI is a local mock and every result is marked **[MOCK]**. With a key, a failure never
falls back to the mock. To use the stronger model, set `VULTR_MODEL=glm-5.3`.

### Run the tests

Unit tests in Python 3.12 (no Docker needed inside):

```sh
docker run --rm -v "$PWD:/src:ro" python:3.12-slim sh -c \
  "mkdir /w && cd /src && tar --exclude=./backend/.venv -cf - backend scenarios | tar -xf - -C /w \
   && cd /w/backend && pip install -q -e '.[dev]' && pytest -q"
```

Real sandbox tests (need Docker and the sandbox image; skipped otherwise):

```sh
cd backend && .venv/bin/python -m pytest -m docker   # Windows: .venv\Scripts\python -m pytest -m docker
```

## Future work

- Sui and other non-EVM chains
- Simulation on a mainnet fork
- Real HSM / MPC integration
- Watchtower: detect a hijacked wallet, pause top-ups, prepare revocation

## Design log

We keep this log so you can see how the idea changed while we built it.

| Date | Change | Why |
|---|---|---|
| 31 Oct | Idea: a guard that runs a signing request in a sandbox before it is signed | Binance hack of 29 Oct; feasibility test showed EIP-7702 delegation can be replayed locally and offline |
| 31 Oct | Deterministic rules decide; the AI only explains | An AI judge can be fooled by text inside the transaction |
| 31 Oct | Fail closed on any error or empty result | Feasibility test showed empty traces can look like success |
| 2 Nov | Guard is a **co-signer**, not an adviser | Binance's signer obeyed a compromised service; a guard that can be skipped would not help |
| 2 Nov | Rules for plain transfers (R5, R6) and a circuit breaker | Most chains in the hack used plain transfers, not EIP-7702 |
| 2 Nov | Fast path + slow path, no AI gate in front | Speed for normal withdrawals; an AI gate could be fooled and is slower |
| 3 Nov | *(build starts)* | |
| 3 Nov | Two kinds of wallets: customer hot wallets (no allowlist, behavior rules) and treasury wallets (strict allowlist) | Customers withdraw to addresses we cannot know; an allowlist there would send every withdrawal to a human |
| 3 Nov | New rules R7 (same address from many chains) and R8 (many assets to one address), plus a DELAY decision | Mercado Bitcoin hack (2 Nov): no EIP-7702, only plain transfers, one address on 10 chains, ~185 tokens one by one |
| 3 Nov | Circuit breaker in steps (address → wallet → safe mode → global), latched | A full stop hurts every customer; both exchanges stopped all withdrawals for days |
| 3 Nov | Watchtower check W2: outflow without Guard approval = stolen key | Some Mercado keys were probably stolen; a co-signer cannot stop that, only detect it |
| 3 Nov | Dropped "quarantine new address" | Every new customer would wait; the cost for customers is higher than the gain |
| 3 Nov | Sandbox starting state comes from the Guard (StateProvider), never from the request | The withdrawal service is untrusted |

## Sources

- TRM Labs – North Korea steals USD 6.36 billion from Binance: https://www.trmlabs.com/resources/blog/north-korea-steals-usd-6-36-billion-from-binance-in-the-largest-hack-in-history
- BleepingComputer – Binance links theft to compromised DevOps laptop: https://bleepingcomputer.com/news/security/binance-links-63-billion-crypto-theft-to-compromised-devops-laptop
- DEV Community – 50 signatures and a 37-minute race: https://dev.to/thevenice/how-a-53b-binance-hot-wallet-hack-worked-eip-7702-50-signatures-and-a-37-minute-race-2lea

## Author

Konstantinos Diamantidis · Computer Science, Democritus University of Thrace · [github.com/KDiamantidis](https://github.com/KDiamantidis)
