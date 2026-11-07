"""F1 and F6 (security audit): parallel requests decide like requests one by one, the per-wallet
lock is never held during the sandbox, and a check that cannot be recorded is never approved."""

import threading
import time
from collections import Counter

from app.models import Decision
from tests.api_helpers import ADMIN, FakeRunner as ApiRunner, client, scenario_request
from tests.test_guard import ETH, HOT, NOW, FakeRunner, guard, req

CHAINS = [1, 10, 42161, 137, 43114]


class SlowRunner(FakeRunner):
    def __init__(self, seconds):
        super().__init__()
        self.seconds = seconds

    def run(self, tx, state):
        time.sleep(self.seconds)
        return super().run(tx, state)


def five_chains(g, to, parallel):
    out = []

    def one(chain):
        out.append(g.check(req(to=to, chain_id=chain, value=ETH), now=NOW).verdict.verdict)

    if parallel:
        ts = [threading.Thread(target=one, args=(c,)) for c in CHAINS]
        [t.start() for t in ts]
        [t.join(30) for t in ts]
    else:
        for c in CHAINS:
            one(c)
    return Counter(v.value for v in out)


def test_parallel_requests_decide_like_one_by_one():
    """The audit's proof (F1): 5 chains to one new address at once used to give 5x ALLOW."""
    seq = five_chains(guard(runner=SlowRunner(0.05)), "0x" + "b1" * 20, parallel=False)
    par = five_chains(guard(runner=SlowRunner(0.05)), "0x" + "b2" * 20, parallel=True)
    assert seq == Counter({"ALLOW": 2, "DELAY": 2, "BLOCK": 1})
    assert par == seq


def test_the_lock_is_never_held_during_the_sandbox():
    g = guard(runner=SlowRunner(0.6))
    t0 = time.perf_counter()
    ts = [threading.Thread(target=g.check, args=(req(to="0x" + f"{0xc0 + i:02x}" * 20, chain_id=1),)) for i in range(3)]
    [t.start() for t in ts]
    [t.join(10) for t in ts]
    assert time.perf_counter() - t0 < 1.5  # 3 sandboxes of 0.6 s for the SAME wallet ran side by side


def test_a_parallel_pending_check_does_not_make_a_destination_known():
    """A provisional row counts for outflow and multi-chain rules, never as 'paid before'."""
    g = guard(runner=SlowRunner(0.5))
    first = threading.Thread(target=g.check, args=(req(to="0x" + "d1" * 20),))
    first.start()
    time.sleep(0.15)
    assert g.history.paid_before(HOT, "0x" + "d1" * 20, before=NOW + 1) is False
    first.join(5)


def test_a_check_that_cannot_be_recorded_is_blocked():
    g = guard()

    def broken(rec):
        raise OSError("database is locked")

    g.history.record_check = broken
    r = g.check(req())
    assert r.verdict.verdict is Decision.BLOCK
    assert any("could not record" in x or "guard error" in x for x in r.verdict.reasons)


def test_a_failed_history_write_is_never_signed(tmp_path):
    c, svc = client(tmp_path, runner=ApiRunner())
    real = svc.history.record_check
    calls = {"n": 0}

    def flaky(rec):
        calls["n"] += 1
        if rec.verdict != "PENDING":  # the final write fails
            raise OSError("disk full")
        return real(rec)

    svc.history.record_check = flaky
    out = c.post("/withdraw", json=scenario_request("a_customer_small_withdrawal"),
                 headers={"X-Admin-Token": ADMIN}).json()
    assert out["check"]["verdict"] == "BLOCK" and out["signer"]["signed"] is False and out["approval"] is None
