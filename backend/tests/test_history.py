import pytest

from history import CheckRecord, History

W, W2 = "0x" + "a1" * 20, "0x" + "a2" * 20
D, D2 = "0x" + "d1" * 20, "0x" + "d2" * 20
T1, T2 = "0x" + "71" * 20, "0x" + "72" * 20
HOUR = 3600.0
_n = 0


def rec(ts, verdict="ALLOW", wallet=W, dest=D, chain=1, asset="native", amount=10, nonces=()):
    global _n
    _n += 1
    return CheckRecord(id=f"c{_n}", ts=ts, wallet=wallet, chain_id=chain, wallet_type="customer_hot",
                       kind="native_transfer", destination=dest, asset=asset, amount=amount, path="fast",
                       path_reasons=["x"], verdict=verdict, findings=[], timings={"total": 0.1}, llm=None,
                       request={"to": dest}, auth_nonces=list(nonces))


@pytest.fixture
def h():
    return History(":memory:")


def test_outflow_counts_only_may_move_funds_in_window(h):
    h.record_check(rec(0, "ALLOW", amount=10))
    h.record_check(rec(10, "DELAY", amount=20))
    h.record_check(rec(20, "NEEDS_HUMAN", amount=40))
    h.record_check(rec(30, "BLOCK", amount=1000))  # never moved
    h.record_check(rec(40, "ALLOW", wallet=W2, amount=5))  # other wallet
    h.record_check(rec(50, "ALLOW", asset=T1, amount=7))  # other asset
    assert h.outflow(W, "native", since=0) == 70
    assert h.outflow(W, "native", since=5) == 60
    assert h.outflow(W, T1, since=0) == 7


def test_big_amounts_survive_sqlite(h):
    h.record_check(rec(0, amount=2**255))
    h.record_check(rec(1, amount=2**255))
    assert h.outflow(W, "native", 0) == 2**256


def test_chains_and_assets_count_every_attempt(h):
    h.record_check(rec(0, chain=1))
    h.record_check(rec(1, chain=10, asset=T1, verdict="BLOCK"))
    h.record_check(rec(2, chain=137, asset=T1))
    h.record_check(rec(3, chain=1, dest=D2))
    assert h.chains_to(D, since=0) == {1, 10, 137}
    assert h.chains_to(D, since=1.5) == {137}
    assert h.assets_to(D, since=0) == {"1:native", f"10:{T1}", f"137:{T1}"}


def test_recent_auth_nonces(h):
    h.record_check(rec(0, nonces=[(W, 1, 7)]))
    h.record_check(rec(5 * HOUR, nonces=[(W, 1, 9), (W2, 1, 3)]))
    assert sorted(h.recent_auth_nonces(W, since=0)) == [(1, 7), (1, 9)]
    assert h.recent_auth_nonces(W, since=HOUR) == [(1, 9)]


def test_freeze_is_latched_until_reset(h):
    assert h.freeze(D, "R8", now=1, check_id="c1") is True
    assert h.freeze(D, "R7", now=2) is False  # already frozen, first reason kept
    assert h.is_frozen(D)["reason"] == "R8"
    assert h.is_frozen(D2) is None
    assert h.unfreeze(D, actor="alice", now=3) is True
    assert h.is_frozen(D) is None
    assert [e["action"] for e in h.events()] == ["freeze_destination", "freeze_destination", "reset_destination"]
    assert h.events()[-1]["actor"] == "alice"


def test_pause_is_latched_until_reset(h):
    h.pause(W, "R1", now=1)
    assert h.is_paused(W)["reason"] == "R1"
    assert [p["wallet"] for p in h.paused()] == [W]
    assert h.unpause(W, actor="bob", now=2) is True
    assert h.unpause(W, actor="bob", now=3) is False
    assert h.paused() == []


def test_checks_round_trip(h, tmp_path):
    db = History(tmp_path / "sub" / "h.sqlite3")
    db.record_check(rec(0, amount=2**200))
    [c] = db.checks()
    assert c["amount"] == 2**200 and c["path_reasons"] == ["x"] and c["llm"] is None
    db.close()
    assert History(tmp_path / "sub" / "h.sqlite3").checks()[0]["id"] == c["id"]  # persisted
