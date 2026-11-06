# Design log

How the idea changed while we built it (dates: October-November 2026). Incident facts as in the README "Motivation" section and its sources.

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
| 3 Nov | New rules R7 (same address from many chains) and R8 (many assets to one address), plus a DELAY decision | Mercado Bitcoin hack (2 Nov): no EIP-7702, only plain transfers, attacker addresses on at least 10 chains, hundreds of tokens (about 185 on Ethereum alone) |
| 3 Nov | Circuit breaker in steps (address → wallet → safe mode → global), latched | A full stop hurts every customer; both exchanges paused withdrawals for all customers |
| 3 Nov | Watchtower check W2: outflow without Guard approval = stolen key | Some Mercado keys were probably stolen; a co-signer cannot stop that, only detect it |
| 3 Nov | Dropped "quarantine new address" | Every new customer would wait; the cost for customers is higher than the gain |
| 3 Nov | Sandbox starting state comes from the Guard (StateProvider), never from the request | The withdrawal service is untrusted |
| 5 Nov | Escalation policy: AI asked only for new destinations or large amounts, at most 3 at once | Asking the AI on every fast-path check caused 288 rate-limit errors (429) in the live load test of 4 Nov (laptop, real `glm-5.3-flash`, 8 at a time) |
| 6 Nov | New destinations go straight to full simulation; the AI is asked only about large amounts; one shared limit of 3 model calls, checks first | On a slow provider day no AI check answered within 2 s, so every new destination was simulated anyway; a simulation (~1–3 s) gives evidence, the AI only an opinion |
| 6 Nov | Neutral attack-class names on the website, real names only in the README | The demo shows classes of attack, not claims about specific incidents |
