"""Held requests: NEEDS_HUMAN waits for a human, DELAY waits for its release time.

Before anything is approved, the request/history rules run again on the held request
(not counting the request itself): a freeze, a pause or a new finding may have appeared
since. "New" = any BLOCK, or any rule that did not cause the hold. Then:
  nothing new -> approval -> mock signer      (status approved / released)
  something new -> no approval, now BLOCK     (status blocked, with the reason)
Every decision is logged with who decided and when. A held request is decided once.
"""

import threading
import time
from typing import Any, Callable

from app.models import Decision, TxRequest, Verdict
from approval import Approver
from guard import Guard
from signer_mock import MockSigner


class HoldError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


class HoldManager:
    def __init__(self, guard: Guard, approver: Approver, signer: MockSigner,
                 publish: Callable[[str, str, dict], None] | None = None, clock=None):
        self.guard, self.approver, self.signer = guard, approver, signer
        self.history = guard.history
        self.publish = publish or (lambda *a: None)
        self.clock = clock or guard.clock or time.time
        self._lock = threading.Lock()  # one decision at a time: no double approval

    # ---- human decisions (NEEDS_HUMAN) -------------------------------------------------

    def approve(self, hold_id: str, actor: str) -> dict[str, Any]:
        with self._lock:
            item = self._pending(hold_id, "needs_human")
            self.publish(hold_id, "human_decision", {"decision": "approve", "by": actor})
            return self._decide(item, actor, final="approved")

    def reject(self, hold_id: str, actor: str) -> dict[str, Any]:
        with self._lock:
            item = self._pending(hold_id, "needs_human")
            now = self.clock()
            self.history.resolve_held(hold_id, "rejected", f"rejected by {actor}", actor, now)
            self.publish(hold_id, "human_decision", {"decision": "reject", "by": actor})
            return {"id": hold_id, "status": "rejected", "signed": False, "by": actor, "at": now}

    # ---- DELAY release -----------------------------------------------------------------

    def release_due(self, now: float | None = None) -> list[dict[str, Any]]:
        """Release every DELAY whose time has come, unless a new signal appeared."""
        now = self.clock() if now is None else now
        out = []
        with self._lock:
            for item in self.history.due_delays(now):
                out.append(self._decide(item, "delay-worker", final="released", now=now))
        return out

    # ---- internals ---------------------------------------------------------------------

    def _pending(self, hold_id: str, kind: str) -> dict[str, Any]:
        item = self.history.get_held(hold_id)
        if item is None:
            raise HoldError(404, "no such held request")
        if item["kind"] != kind:
            raise HoldError(409, f"this is a {item['kind']} hold; it is released by the DELAY worker")
        if item["status"] != "pending":
            raise HoldError(409, f"already decided: {item['status']}")
        return item

    def _decide(self, item: dict[str, Any], actor: str, final: str, now: float | None = None) -> dict[str, Any]:
        now = self.clock() if now is None else now
        tx = TxRequest.model_validate(item["request"])
        findings = self.guard.recheck(tx, exclude=item["id"], now=now)
        caused = set(item["rule_ids"])
        new = [f for f in findings if f.action is Decision.BLOCK or f.rule_id not in caused]
        base = {"id": item["id"], "kind": item["kind"], "by": actor, "at": now}
        if new:
            why = "; ".join(f"[{f.rule_id}] {f.title}" for f in new)
            outcome = f"not released: new signal since the hold, now BLOCK: {why}"
            self.history.resolve_held(item["id"], "blocked", outcome, actor, now)
            self.publish(item["id"], "hold_blocked", {"by": actor, "reason": outcome,
                                                      "new_findings": [f.rule_id for f in new]})
            return {**base, "status": "blocked", "verdict": "BLOCK", "signed": False, "reason": outcome,
                    "new_findings": [f.model_dump(mode="json") for f in new]}

        allow = Verdict(verdict=Decision.ALLOW, reasons=[], findings=[], trace_id=item["id"])
        approval = self.approver.issue(tx, allow)
        signed = self.signer.sign(tx, approval)
        how = "approved by a human" if final == "approved" else "released after DELAY"
        outcome = f"{how}; re-check found nothing new; {signed.reason}"
        self.history.resolve_held(item["id"], final, outcome, actor, now,
                                  approval_id=approval.approval_id, signed=signed.signed)
        self.publish(item["id"], "approval", {"issued": True, "approval_id": approval.approval_id,
                                              "basis": how, "by": actor})
        self.publish(item["id"], "signer", {"signed": signed.signed, "reason": signed.reason})
        return {**base, "status": final, "verdict": "ALLOW", "signed": signed.signed, "reason": outcome,
                "approval_id": approval.approval_id}
