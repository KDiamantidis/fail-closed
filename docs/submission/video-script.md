# Video script (target about 4:20)

Read slowly. One sentence at a time. **[CLICK]** = what to do on screen. Words in [brackets]
are how to say a hard word; do not read them. Times are speaking time at 110 words per minute;
clicks and waiting add a little.

Before you record: page **Overview** → **Reset demo** (the strip says "Normal"). Plan B: run
`.\deploy\planb.ps1 redteam` once first, so the Containment page has its report.

Slides: `docs/slides/fail-closed-vm.pdf`, or `fail-closed-laptop.pdf` for Plan B.
Spoken lines only, for reading: `docs/submission/video-read.txt`.

---

## 0:00 · The problem (slides 2 and 3)

**[CLICK]** Show slide 2.

> In 2026, two exchanges lost money through valid signatures.
> At Binance [BY-nance], about 6.3 billion dollars left hot wallets.
> One transaction carried fifty EIP-7702 [E-I-P seventy-seven oh two] authorizations.
> One of them handed a hot wallet to the attacker's contract.
> At Mercado Bitcoin [mer-KAH-doh], plain transfers sent funds to attacker addresses on at least ten chains.

**[CLICK]** Show slide 3.

> Then both exchanges paused withdrawals for all customers.
> Honest customers paid too.
> Fail Closed is designed to reject this class of attack.
> We do not claim it would have stopped these incidents.

## 0:45 · The idea (slide 4)

**[CLICK]** Show slide 4.

> Our idea is simple: no approval, no signature.
> Fail Closed is an independent co-signer.
> It checks what a signature will do, not who asks.
> Fixed rules decide.
> The AI explains.
> It never decides.

## 1:03 · How it works (slide 5)

**[CLICK]** Show slide 5. Point at the boxes from left to right.

> A plain payout to a known customer passes the rules in milliseconds.
> Anything unusual runs first in an isolated sandbox, with no network and no secrets.
> The rule engine decides: allow, delay, needs review, or block.
> Only an allow gets a signed approval, and the signer signs only with that approval.
> The AI runs on Vultr [vulture]. It explains, and it may ask for a closer look.

**Read ONE of these two lines:**

> **If the VM worked:** The Guard and the sandboxes run on a Vultr [vulture] Cloud Compute VM. The AI runs on Vultr Serverless Inference.
>
> **If Plan B (laptop):** Our Vultr [vulture] VM was blocked by account review, so today the Guard runs on my laptop. The AI still runs on Vultr Serverless Inference.


## 1:50 · Demo 1: delegation drain

**[CLICK]** Switch to the app, page **Overview**. The risk strip at the top says "Normal".
**[CLICK]** Card **Incident replays** → **Run replay** next to **Hot wallet delegation drain (EIP-7702)**.

> A compromised service asks the hot wallet to sign five delegation authorizations.

**[CLICK]** Under the replay: **View requests →**, then click the one row in the table.

> Blocked.
> The wallet would hand control to an unknown contract.
> Nothing was signed.

**[CLICK]** Scroll to the card **Evidence from the sandbox**.

> In the sandbox, a test call moves 100 ETH [E-T-H] out.
> Fake funds, of course.

**[CLICK]** Scroll to the card **AI analysis**. WAIT until it shows text instead of "Analysis in progress" (by polling this can take 5-15 seconds; the header says "Updates every 2 s").

> The AI explains in plain words.
> It did not decide.

**[CLICK]** Point at the strip at the top.

> The risk strip is now orange: High, one wallet paused.

## 2:22 · Demo 2: coordinated attack, risk level 0 to 4

**[CLICK]** Sidebar **Overview**. In the card **Incident replays**, click **Reset demo**. The strip says "Normal".
**[CLICK]** **Run replay** next to **Coordinated attack on several wallets and chains**. Point at the strip while it changes colour (it may change in steps every 2 s).

> Now several wallets and chains are attacked.
> Watch the risk level.
> Level one freezes one address.
> Level two pauses one wallet.
> Level three puts one chain in safe mode.
> Customers outside the stopped scope keep being paid.
> Level four, the global stop, needs two independent signals.

**[CLICK]** Point at the deep red banner "Global stop: no withdrawals are being signed". Do NOT type the admin token on camera.

> Only an operator can lift it, with the admin token.

**[CLICK]** Click **Reset demo** (in the banner or in the card **Incident replays**).


## 2:52 · Containment

**[CLICK]** Sidebar **Containment** (page "Sandbox isolation report"; it shows the saved run).

> We also attack the sandbox itself.
> It cannot reach the internet, find keys or secrets, change files, or gain privileges.
> A fork bomb, a memory bomb and a CPU that spins forever are all stopped.
> Seven of seven are contained.

## 3:14 · Measured results and business value (slides 9 and 10)

**[CLICK]** Show slide 9.

> We measured five hundred normal withdrawals and one hidden attack, on a laptop.
> The attack was stopped.
> No normal customer was refused.
> Plain payouts take about three milliseconds.
> A sandbox check takes one to three seconds.

**[CLICK]** Show slide 10.

> So normal customers keep being paid, and the breaker stops as little as possible.

## 3:41 · Where it fits (slide 6)

**[CLICK]** Show slide 6.

> Signer hooks already exist.
> Fail Closed is a prototype of the decision logic behind them.

## 3:50 · Honest limits and next (slide 11)

**[CLICK]** Show slide 11.

> This is a prototype on a test chain with fake funds.
> It does not stop the first break-in.
> It stops that break-in from getting a signature.
> Next: a real signer hook, and checking every signature against the exchange ledger.

## 4:11 · Close (slide 1)

**[CLICK]** Show slide 1.

> Fail Closed.
> No approval, no signature.
> Thank you.

---

Spoken words: 472 (counting the longer where-it-runs line). At 110 words per minute: 4:17 of speaking.
