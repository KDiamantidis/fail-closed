"""Circuit breaker levels 3 (safe mode per chain) and 4 (global stop): latched, reset, logged."""

import pytest

from app.config import Settings
from app.models import Decision, Finding, TxRequest, Verdict
from breaker import Breaker
from history import CheckRecord, History
from tests.api_helpers import ADMIN, SCENARIOS, FakeRunner, client

S = Settings(safe_mode_paused_wallets=2, safe_mode_evidence_blocks=3, safe_mode_window_minutes=60)
NOW = 1_000_000.0
_n = 0


def tx(wallet, chain=1):
    return TxRequest.model_validate({"wallet_type": "customer_hot", "chain_id": chain, "from": wallet,
                                     "to": "0x" + "ba" * 20, "value": 1})


def evidence_block(h: History, b: Breaker, wallet: str, chain: int, now: float, rule="R1") -> list[str]:
    """Record a BLOCK check with sandbox evidence and let the breaker react to it."""
    global _n
    _n += 1
    cid = f"c{_n}"
    f = Finding(rule_id=rule, action=Decision.BLOCK, title=f"{rule} evidence")
    v = Verdict(verdict=Decision.BLOCK, reasons=["x"], findings=[f], trace_id=cid)
    h.record_check(CheckRecord(id=cid, ts=now, wallet=wallet, chain_id=chain, wallet_type="customer_hot",
                               kind="delegation", destination=wallet, asset="native", amount=0, path="slow",
                               path_reasons=[], verdict="BLOCK", findings=[f.model_dump(mode="json")],
                               timings={}, llm=None, request={}))
    return b.apply(tx(wallet, chain), v, now, cid)


W = [f"0x{i:040x}" for i in range(1, 9)]


def test_two_paused_wallets_on_a_chain_enter_safe_mode():
    h = History()
    b = Breaker(h, S)
    assert evidence_block(h, b, W[0], 1, NOW) == [f"level 2: paused wallet {W[0]} (R1)"]
    assert h.safe_modes() == []
    actions = evidence_block(h, b, W[1], 1, NOW + 60)
    assert "level 3: safe mode on chain 1" in actions[-1]
    assert h.is_safe_mode(1) and not h.is_safe_mode(10)


def test_paused_wallets_on_other_chains_do_not_count():
    h = History()
    b = Breaker(h, S)
    evidence_block(h, b, W[0], 1, NOW)
    evidence_block(h, b, W[1], 10, NOW)
    assert h.safe_modes() == []


def test_window_matters():
    h = History()
    b = Breaker(h, S)
    evidence_block(h, b, W[0], 1, NOW)
    evidence_block(h, b, W[1], 1, NOW + 61 * 60)  # the first pause is outside the 60 min window
    assert h.safe_modes() == []


def test_evidence_blocks_alone_can_trigger_safe_mode():
    h = History()
    b = Breaker(h, S)
    h.pause(W[0], "R1 BLOCK", now=NOW)  # the same wallet: only one paused wallet in total
    for i in range(3):
        evidence_block(h, b, W[0], 1, NOW + i)
    assert h.is_safe_mode(1)["reason"].startswith("3 sandbox-evidence BLOCKs")


def test_two_independent_signals_global_stop_and_one_is_not_enough():
    h = History()
    b = Breaker(h, S)
    evidence_block(h, b, W[0], 1, NOW)
    evidence_block(h, b, W[1], 1, NOW)
    assert h.global_stop() is None  # one signal (safe mode on chain 1)
    evidence_block(h, b, W[2], 10, NOW)
    actions = evidence_block(h, b, W[3], 10, NOW)
    assert any(a.startswith("level 4: GLOBAL STOP") for a in actions)
    assert h.global_stop()["signals"] == ["safe mode on chain 1", "safe mode on chain 10"]


def test_red_team_breach_is_a_signal():
    h = History()
    b = Breaker(h, S)
    evidence_block(h, b, W[0], 1, NOW)
    evidence_block(h, b, W[1], 1, NOW)
    h.save_redteam("rt1", NOW, {"all_contained": False, "red": ["network"], "results": []})
    assert b.evaluate_global(NOW)[0].startswith("level 4: GLOBAL STOP")
    assert "red-team payload not contained: network" in h.global_stop()["signals"]


def test_resets_are_latched_and_logged():
    h = History()
    b = Breaker(h, S)
    for w, c in ((W[0], 1), (W[1], 1), (W[2], 10), (W[3], 10)):
        evidence_block(h, b, w, c, NOW)
    assert h.global_stop()
    assert b.reset_global("ops-lead", NOW + 10) and h.global_stop() is None
    assert h.safe_modes()  # resetting level 4 does not end safe mode: each step is separate
    assert b.reset_safe_mode(1, "ops-lead", NOW + 11) and b.reset_safe_mode(10, "ops-lead", NOW + 12)
    assert not b.reset_safe_mode(10, "ops-lead", NOW + 13)
    events = [(e["action"], e["actor"]) for e in h.events() if e["action"].startswith("reset")]
    assert events == [("reset_global_stop", "ops-lead"), ("reset_safe_mode", "ops-lead"),
                      ("reset_safe_mode", "ops-lead")]


def test_f3_and_f4_rules():
    from decode import decode
    from policies.engine import RuleContext
    from policies.request_rules import f3_safe_mode_chain, f4_global_stop

    h = History()
    ctx = RuleContext(tx=tx(W[0], 1), path=None, simulation=None, decoded=decode(tx(W[0], 1), []),
                      settings=S, history=h, now=NOW)
    assert f3_safe_mode_chain(ctx) == [] and f4_global_stop(ctx) == []
    h.enter_safe_mode(1, "test", NOW)
    h.set_global_stop("test", ["a", "b"], NOW)
    assert f3_safe_mode_chain(ctx)[0].action is Decision.NEEDS_HUMAN
    assert f4_global_stop(ctx)[0].action is Decision.BLOCK


@pytest.mark.skipif(not SCENARIOS.exists(), reason="scenarios/ not available")
def test_api_reset_levels_3_and_4_need_admin(tmp_path):
    c, svc = client(tmp_path, runner=FakeRunner())
    svc.history.enter_safe_mode(1, "test", NOW)
    svc.history.set_global_stop("test", ["a", "b"], NOW)
    state = c.get("/breaker").json()
    assert state["global_stop"]["signals"] == ["a", "b"] and state["safe_mode_chains"][0]["chain_id"] == 1
    assert c.post("/breaker/reset", json={"global_stop": True}).status_code == 401
    assert c.post("/breaker/reset", json={"chain_id": 1, "wallet": "0x1"},
                  headers={"X-Admin-Token": ADMIN}).status_code == 422
    r = c.post("/breaker/reset", json={"global_stop": True, "actor": "ops-lead"}, headers={"X-Admin-Token": ADMIN})
    assert r.json()["reset"] is True and svc.history.global_stop() is None
    assert c.post("/breaker/reset", json={"chain_id": 1}, headers={"X-Admin-Token": ADMIN}).json()["reset"]
    assert svc.history.events()[-2]["actor"] == "ops-lead"


@pytest.mark.skipif(not SCENARIOS.exists(), reason="scenarios/ not available")
def test_scenario_g_climbs_1_2_3_4_and_recovers(tmp_path):
    from cli import expect_status
    from scenario import load

    sc = load(SCENARIOS / "g_coordinated_breaker.json")
    c, svc = client(tmp_path, runner=FakeRunner())
    g = svc.guard
    for r in sc.requests:
        now = NOW + r.t
        if r.is_action:
            assert r.apply(g.breaker, now)
            assert expect_status(r, None, g.history) == "OK", r.label
            continue
        res = g.check(r.tx(), now=now)
        assert expect_status(r, res, g.history) == "OK", (r.label, res.verdict.reasons)
    # the replay ends at the global stop; an admin reset brings everything back to level 0
    from breaker import level_of

    assert level_of(g.breaker.state()) == 4
    done = g.breaker.reset_all(actor="ops-lead", now=NOW + 900)
    assert done[0] == "global stop lifted" and any("safe mode ended" in d for d in done)
    assert g.breaker.state() == {"frozen_destinations": [], "paused_wallets": [], "safe_mode_chains": [],
                                 "global_stop": None}
    after = g.check(TxRequest.model_validate({
        "wallet_type": "customer_hot", "chain_id": 137, "from": "0x1000000000000000000000000000000000000706",
        "to": "0xc000000000000000000000000000000000000702", "value": 10**18}), now=NOW + 960)
    assert after.verdict.verdict is Decision.ALLOW
    assert all(e["actor"] == "ops-lead" for e in g.history.events() if e.get("kind", "").startswith("reset"))


def test_replay_reports_the_level_step_by_step_and_admin_lifts_it(tmp_path):
    c, _ = client(tmp_path, runner=FakeRunner())
    run_id = "risklevel01"
    out = c.post(f"/scenarios/g_coordinated_breaker/run?run_id={run_id}").json()
    assert out["breaker"]["global_stop"]  # the replay stops at the global stop
    levels = [e["data"]["level"] for e in c.get(f"/events?run_id={run_id}").json() if e["type"] == "breaker_state"]
    assert levels == sorted(levels) and levels[-1] == 4 and {0, 1, 2, 3} <= set(levels)  # one per step
    # lifting it is still an admin action, even in a visitor's own replay
    assert c.post(f"/demo/{run_id}/breaker/reset-all").status_code == 401
    assert c.post(f"/demo/{run_id}/breaker/reset-all", headers={"x-admin-token": "wrong"}).status_code == 403
    r = c.post(f"/demo/{run_id}/breaker/reset-all", headers={"x-admin-token": ADMIN}).json()
    assert r["level"] == 0 and r["reset"][0] == "global stop lifted"
    assert c.get("/status").json()["breaker_level"] == 0  # the main state was never touched
    assert c.post("/breaker/reset-all", headers={"x-admin-token": ADMIN}).json() == {
        "reset": [], "breaker": {"frozen_destinations": [], "paused_wallets": [], "safe_mode_chains": [],
                                 "global_stop": None}, "level": 0}
