"""Circuit breaker, levels 1-2. Every step is latched until a human resets it.

Level 1: freeze one destination address (requested by R7 >= 5 chains or R8).
Level 2: pause one wallet on R1, R2, or R3 with sandbox evidence. Only the simulation-level
         R3 can BLOCK; the request-level R3 (Guard balances only) is NEEDS_HUMAN and never
         pauses, so an honest large withdrawal does not stop the other customers.
Levels 3-4 (safe mode per chain, global stop with >= 2 independent signals): Phase 3.

The freeze / pause takes effect on the next request through rules F1 / F2.
"""

from app.models import Decision, Effect, TxRequest, Verdict
from decode import authority_of
from history import History

PAUSE_RULES = {"R1", "R2", "R3"}


class Breaker:
    def __init__(self, history: History):
        self.history = history

    def apply(self, tx: TxRequest, verdict: Verdict, now: float, check_id: str) -> list[str]:
        """Apply the effects of one verdict. Returns what changed, for display."""
        actions: list[str] = []
        if verdict.verdict is not Decision.BLOCK:
            return actions

        for f in verdict.findings:
            if f.effect is Effect.FREEZE_DESTINATION and f.action is Decision.BLOCK and f.subject:
                if self.history.freeze(f.subject, f"{f.rule_id}: {f.title}", now, check_id):
                    actions.append(f"level 1: froze destination {f.subject} ({f.rule_id})")

        triggers = sorted({f.rule_id for f in verdict.findings
                           if f.rule_id in PAUSE_RULES and f.action is Decision.BLOCK})
        if triggers:
            wallets = [tx.from_] + [authority_of(a, tx) for a in tx.authorization_list or []]
            for w in dict.fromkeys(wallets):
                if self.history.pause(w, f"{'+'.join(triggers)} BLOCK", now, check_id):
                    actions.append(f"level 2: paused wallet {w} ({'+'.join(triggers)})")
        return actions

    def reset_destination(self, address: str, actor: str, now: float) -> bool:
        return self.history.unfreeze(address.lower(), actor=actor, now=now)

    def reset_wallet(self, wallet: str, actor: str, now: float) -> bool:
        return self.history.unpause(wallet.lower(), actor=actor, now=now)
