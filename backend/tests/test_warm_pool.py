"""Warm sandbox pool: clean check before reuse, never reused after an error, max uses,
and the same diff as a fresh container."""

import pytest

from app.config import Settings
from app.models import TxRequest
from sandbox_runner import ANVIL_ACCOUNTS, SandboxError, SandboxRunner
from state_provider import StartingState
from tests.fake_docker import FakeChain, FakeDocker
from tests.helpers import BOB, tx

HOT = ANVIL_ACCOUNTS[1]
SWEEPER = "0x00000000000000000000000000000000005eeeee"
ETH = 10**18
STATE = StartingState.model_validate({"accounts": {HOT: {"balance": 5 * ETH, "nonce": 3}}})
REQ = TxRequest.model_validate(tx(**{"from": HOT, "to": BOB, "value": ETH}))


def warm_runner(chain_factory=FakeChain, **kw):
    d = FakeDocker(chain_factory=chain_factory)
    s = Settings(sandbox_mode="warm", sandbox_pool_size=1, sandbox_timeout_seconds=5, **kw)
    return SandboxRunner(s, client=d), d


def test_container_is_reused_and_state_is_reverted():
    r, d = warm_runner()
    first = r.run(REQ, STATE)
    second = r.run(REQ, STATE)
    assert len(d.containers.created) == 1  # one container served both checks
    assert "container_start" in first.timings and "pool_acquire" in second.timings
    assert first.state_diff == second.state_diff
    chain = d.containers.chains[0]
    assert chain.balance.get(HOT, 0) == 0 and chain.block == 0  # reverted to clean
    assert chain.chain_id == REQ.chain_id
    assert r.pool.stats["reused"] == 1 and not d.containers.created[0].removed.is_set()


def test_dirty_state_is_detected_and_the_container_destroyed():
    def leaky():
        c = FakeChain()
        c.revert_noop = True  # the revert "succeeds" but the state stays behind
        return c

    r, d = warm_runner(chain_factory=leaky)
    r.run(REQ, STATE)
    r.run(REQ, STATE)
    assert r.pool.stats["dirty"] == 1
    assert d.containers.created[0].removed.is_set()  # destroyed, not reused
    assert len(d.containers.created) >= 2  # the second check got a fresh one


def test_never_reused_after_an_error():
    calls = {"n": 0}

    def first_fails():
        c = FakeChain()
        calls["n"] += 1
        if calls["n"] == 1:
            c.fail_on = "debug_traceTransaction"
        return c

    r, d = warm_runner(chain_factory=first_fails)
    with pytest.raises(SandboxError):
        r.run(REQ, STATE)
    assert d.containers.created[0].removed.is_set() and r.pool.stats["discarded"] == 1
    r.run(REQ, STATE)
    assert d.containers.created[0] not in [c for c in d.containers.created[1:]]
    assert d.containers.created[-1].chain.sends  # the next check ran on another container


def test_never_reused_after_a_timeout():
    def hangs():
        c = FakeChain()
        c.block_on = "cast send"
        return c

    r, d = warm_runner(chain_factory=hangs)
    r.settings.sandbox_timeout_seconds = 0.3
    with pytest.raises(SandboxError, match="timeout"):
        r.run(REQ, STATE)
    assert d.containers.created[0].removed.is_set() and r.pool.stats["discarded"] == 1


def test_max_uses_is_respected():
    r, d = warm_runner(sandbox_max_uses=2)
    for _ in range(5):
        r.run(REQ, STATE)
    # uses 1,2 -> retired; 3,4 -> retired; 5 -> a third container
    assert r.pool.stats["retired"] >= 2
    assert d.containers.created[0].removed.is_set() and d.containers.created[1].removed.is_set()
    used = [c for c in d.containers.created if c.chain.sends]
    assert all(len(c.chain.sends) <= 2 for c in used)


def test_max_age_is_respected():
    r, d = warm_runner(sandbox_max_age_seconds=0.0)
    r.run(REQ, STATE)
    r.run(REQ, STATE)
    assert r.pool.stats["reused"] == 0 and r.pool.stats["retired"] >= 1


def test_bad_mode_is_rejected():
    with pytest.raises(ValueError, match="fresh or warm"):
        SandboxRunner(Settings(sandbox_mode="lukewarm"))


# --- real Docker: warm == fresh -----------------------------------------------------------------

@pytest.mark.docker
def test_warm_and_fresh_give_the_same_diff():
    state = StartingState.model_validate({"accounts": {
        HOT: {"balance": 100 * ETH, "nonce": 6}, SWEEPER: {"code": "artifact:MockSweeper"}}})
    delegation = TxRequest.model_validate(tx(**{"from": HOT, "to": HOT, "data": "0x", "value": 0,
                                                "authorization_list": [{"chain_id": 1, "address": SWEEPER,
                                                                        "nonce": n} for n in range(3, 8)]}))
    plain = TxRequest.model_validate(tx(**{"from": ANVIL_ACCOUNTS[0], "to": BOB, "value": ETH, "chain_id": 137}))
    plain_state = StartingState.model_validate({"accounts": {ANVIL_ACCOUNTS[0]: {"balance": 2 * ETH}}})

    fresh = SandboxRunner(Settings(sandbox_mode="fresh"))
    warm = SandboxRunner(Settings(sandbox_mode="warm", sandbox_pool_size=1))
    try:
        warm.start_pool()
        for _ in range(2):  # twice: the second run reuses the reverted container
            for req, st in ((delegation, state), (plain, plain_state)):
                a, b = fresh.run(req, st), warm.run(req, st)
                assert a.state_diff == b.state_diff and a.probe[:1] == b.probe[:1] or (
                    [p["moved"] for p in a.probe] == [p["moved"] for p in b.probe])
                assert a.receipt["status"] == b.receipt["status"]
        assert warm.pool.stats["reused"] >= 3 and warm.pool.stats["dirty"] == 0
    finally:
        warm.pool.shutdown()
