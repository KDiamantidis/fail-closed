"""Circuit breaker, levels 1-4. Every step is latched until a human resets it (logged).

Level 1: freeze one destination address (requested by R7 >= 5 chains or R8).
Level 2: pause one wallet on R1, R2, or R3 with sandbox evidence. Only the simulation-level
         R3 can BLOCK; the request-level R3 (Guard balances only) is NEEDS_HUMAN and never
         pauses, so an honest large withdrawal does not stop the other customers.
Level 3: safe mode on one chain when, within SAFE_MODE_WINDOW_MINUTES, at least
         SAFE_MODE_PAUSED_WALLETS wallets were paused by checks on that chain, or at least
         SAFE_MODE_EVIDENCE_BLOCKS R1/R2/R3-evidence BLOCKs happened there. In safe mode there
         is no fast path on that chain and every request there is NEEDS_HUMAN (still checked).
Level 4: global stop, only with >= GLOBAL_STOP_SIGNALS independent signals: safe mode on a
         chain (one signal per chain) or a red-team payload that was NOT contained. Then no
         approvals at all (BLOCK "global stop") until a human resets it.

The effects reach the next request through rules F1 (frozen), F2 (paused), F3 (safe mode),
F4 (global stop).
"""

from typing import Any

from app.models import Decision, Effect, TxRequest, Verdict
from decode import authority_of
from history import History

PAUSE_RULES = {"R1", "R2", "R3"}
MINUTE = 60.0


class Breaker:
    def __init__(self, history: History, settings: Any = None):
        self.history = history
        self.settings = settings  # None: levels 3-4 use the default thresholds

    def _s(self, name: str, default: Any) -> Any:
        return getattr(self.settings, name, default) if self.settings is not None else default

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

        actions += self._level3(tx.chain_id, now, check_id)
        actions += self.evaluate_global(now)
        return actions

    def _level3(self, chain_id: int, now: float, check_id: str) -> list[str]:
        if self.history.is_safe_mode(chain_id):
            return []
        since = now - self._s("safe_mode_window_minutes", 60) * MINUTE
        paused = self.history.paused_wallets_on_chain(chain_id, since)
        evidence = self.history.evidence_blocks(chain_id, since)
        need_paused = self._s("safe_mode_paused_wallets", 2)
        need_evidence = self._s("safe_mode_evidence_blocks", 3)
        if len(paused) >= need_paused:
            reason = f"{len(paused)} wallets paused on chain {chain_id} within the window"
        elif evidence >= need_evidence:
            reason = f"{evidence} sandbox-evidence BLOCKs (R1/R2/R3) on chain {chain_id} within the window"
        else:
            return []
        if self.history.enter_safe_mode(chain_id, reason, now, check_id):
            return [f"level 3: safe mode on chain {chain_id} ({reason})"]
        return []

    def signals(self) -> list[str]:
        """Independent signals for level 4."""
        out = [f"safe mode on chain {m['chain_id']}" for m in self.history.safe_modes()]
        last = self.history.last_redteam()
        if last is not None and last.get("red"):
            out.append(f"red-team payload not contained: {', '.join(last['red'])}")
        return out

    def evaluate_global(self, now: float) -> list[str]:
        if self.history.global_stop() is not None:
            return []
        sig = self.signals()
        if len(sig) < self._s("global_stop_signals", 2):
            return []
        reason = f"{len(sig)} independent signals: " + "; ".join(sig)
        if self.history.set_global_stop(reason, sig, now):
            return [f"level 4: GLOBAL STOP ({reason})"]
        return []

    # ---- human resets (each logged with who) -------------------------------------------

    def reset_destination(self, address: str, actor: str, now: float) -> bool:
        return self.history.unfreeze(address.lower(), actor=actor, now=now)

    def reset_wallet(self, wallet: str, actor: str, now: float) -> bool:
        return self.history.unpause(wallet.lower(), actor=actor, now=now)

    def reset_safe_mode(self, chain_id: int, actor: str, now: float) -> bool:
        return self.history.exit_safe_mode(int(chain_id), actor=actor, now=now)

    def reset_global(self, actor: str, now: float) -> bool:
        return self.history.clear_global_stop(actor=actor, now=now)

    def reset_all(self, actor: str, now: float) -> list[str]:
        """Back to level 0: lift the global stop, end every safe mode, unpause every wallet,
        unfreeze every destination. Each reset is logged with who did it. Returns what was reset."""
        done: list[str] = []
        if self.reset_global(actor, now):
            done.append("global stop lifted")
        for m in self.history.safe_modes():
            if self.reset_safe_mode(m["chain_id"], actor, now):
                done.append(f"safe mode ended on chain {m['chain_id']}")
        for w in self.history.paused():
            if self.reset_wallet(w["wallet"], actor, now):
                done.append(f"wallet {w['wallet']} unpaused")
        for f in self.history.frozen():
            if self.reset_destination(f["address"], actor, now):
                done.append(f"destination {f['address']} unfrozen")
        return done

    def state(self) -> dict[str, Any]:
        return {"frozen_destinations": self.history.frozen(), "paused_wallets": self.history.paused(),
                "safe_mode_chains": self.history.safe_modes(), "global_stop": self.history.global_stop()}


def level_of(state: dict[str, Any]) -> int:
    """0 normal, 1 a destination frozen, 2 a wallet paused, 3 a chain in safe mode, 4 global stop."""
    if state.get("global_stop"):
        return 4
    if state.get("safe_mode_chains"):
        return 3
    if state.get("paused_wallets"):
        return 2
    if state.get("frozen_destinations"):
        return 1
    return 0
