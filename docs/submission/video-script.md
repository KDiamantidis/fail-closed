# Video script (under 5 minutes)

Read it aloud slowly. Short sentences. **[CLICK]** = what to do on screen. Before you record:
press **Reset demo** on the Overview page, so the risk level is 0 (Normal).

Slides: `docs/slides/fail-closed-vm.pdf`, or `fail-closed-laptop.pdf` if the demo runs on the
laptop (Plan B). The app: the demo address, or http://127.0.0.1:8000.

---

## 0:00 – 0:40 · The problem (slides 2 and 3)

**[CLICK]** Show slide 2.

> In 2026, two exchanges lost money through valid signatures.
> At Binance, about 6.3 billion dollars left hot wallets.
> One transaction carried fifty EIP-7702 authorizations. One of them handed a hot wallet to the
> attacker's contract.
> At Mercado Bitcoin, plain transfers sent funds to attacker addresses on at least ten chains.

**[CLICK]** Show slide 3.

> Then both exchanges paused withdrawals for all customers. Honest customers paid too.
> Fail Closed is designed to reject this class of attack. We do not claim it would have stopped
> these incidents.

## 0:40 – 1:05 · The idea (slide 4)

**[CLICK]** Show slide 4.

> Our idea is simple: no approval, no signature.
> Fail Closed is an independent co-signer. It checks what a signature will do, not who asks.
> Fixed rules decide. The AI explains. It never decides.

## 1:05 – 1:35 · How it works (slide 5)

**[CLICK]** Show slide 5. Point at each box from left to right.

> A withdrawal request comes in. Plain payouts to known customers pass the rules in milliseconds.
> Anything unusual runs first in an isolated sandbox: no network, no secrets, fake funds.
> The rule engine decides: allow, delay, needs review, or block.
> Only an allow gets a signed approval. The signer signs only with that approval.
> The AI runs on Vultr Serverless Inference. It explains, and it may ask for a deeper check.

## 1:35 – 2:25 · Demo 1: delegation drain

**[CLICK]** Switch to the app, Overview page. Risk strip says "Normal".
**[CLICK]** "Run replay" next to **Hot wallet delegation drain (EIP-7702)**.

> A compromised service asks the hot wallet to sign five delegation authorizations.

**[CLICK]** "View requests", then the request row.

> Blocked. The wallet would hand control to an unknown contract. Nothing was signed.

**[CLICK]** Scroll to **Evidence from the sandbox**.

> The sandbox shows the change: the wallet now runs the attacker's code. A test call moves
> 100 ETH out, in the sandbox, with fake funds.

**[CLICK]** Scroll to **AI analysis**.

> The AI explains in plain words. It did not decide. The risk strip at the top is now orange:
> High, one wallet paused.

## 2:25 – 3:15 · Demo 2: coordinated attack, risk 0 to 4

**[CLICK]** Overview. **[CLICK]** "Reset demo". The strip says "Normal" again.
**[CLICK]** "Run replay" next to **Coordinated attack on several wallets and chains**. Point at the
risk strip while it changes colour.

> Now several wallets and chains are attacked. Watch the risk level.
> Level 1 freezes one address. Level 2 pauses one wallet. Level 3 puts one chain in safe mode.
> Customers outside the stopped scope keep being paid.
> Level 4, the global stop, needs two independent signals.

**[CLICK]** Show the deep red banner.

> Only an operator can lift it, with the admin token. For visitors, Reset demo starts again.

**[CLICK]** "Reset demo".

## 3:15 – 3:40 · Containment

**[CLICK]** Sidebar: **Containment**.

> We also attack the sandbox itself: network, secrets, writes, a fork bomb, a memory bomb, CPU
> and privilege escalation. Seven of seven are contained.

## 3:40 – 4:05 · Measured results (slide 9)

**[CLICK]** Show slide 9.

> We measured five hundred normal withdrawals and one hidden attack on a laptop.
> The attack was stopped. Zero normal customers were refused.
> Plain payouts take about three milliseconds. A full sandbox check takes about one to three seconds.

## 4:05 – 4:25 · Where it fits (slide 6)

**[CLICK]** Show slide 6.

> Screening products and signer hooks already exist. The hooks give the enforcement point.
> Fail Closed is a prototype of the decision logic behind it.

## 4:25 – 4:50 · Honest limits and next (slide 11)

**[CLICK]** Show slide 11.

> This is a prototype on a test chain with fake funds. It does not stop the first break-in. It
> stops that break-in from getting a signature.
> Next: plug into a real signer hook, and check every signature against the exchange ledger.

## 4:50 – 5:00 · Close (slide 1)

**[CLICK]** Show slide 1.

> Fail Closed. No approval, no signature. Thank you.
