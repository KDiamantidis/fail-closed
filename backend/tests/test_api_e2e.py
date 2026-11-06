"""End to end through the HTTP API: withdrawal service -> Guard -> approval -> mock signer.

Each flow runs twice: with a fake sandbox (always), and with the real Docker sandbox
(marker `docker`, skipped without Docker or the sandbox image)."""

import pytest

from app.models import TxRequest
from tests.api_helpers import ADMIN, SCENARIOS, FakeRunner, client, scenario_request

pytestmark = pytest.mark.skipif(not SCENARIOS.exists(), reason="scenarios/ not available")

HOT_D = "0x70997970c51812dc3a010c7d01b50e0d17dc79c8"
HOT_F = "0x1000000000000000000000000000000000000f01"


@pytest.fixture(params=["fake", pytest.param("docker", marks=pytest.mark.docker)])
def api(request, tmp_path):
    runner = FakeRunner() if request.param == "fake" else None  # None = real SandboxRunner
    return client(tmp_path, runner=runner)


def withdraw(c, body):
    r = c.post("/withdraw", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def paused(c) -> set[str]:
    return {w["wallet"] for w in c.get("/breaker").json()["paused_wallets"]}


def test_a_small_withdrawal_is_signed(api):
    c, _ = api
    # a new customer: full simulation first (escalation policy), then signed
    out = withdraw(c, scenario_request("a_customer_small_withdrawal"))
    assert out["check"]["verdict"] == "ALLOW" and out["check"]["path"] == "slow"
    assert out["check"]["path_reasons"][-1] == "New destination: full simulation"
    assert out["approval"] is not None
    assert out["signer"]["signed"] is True and out["signer"]["signature"]
    # the same customer again: a known destination and a small amount, rules alone, signed
    again = withdraw(c, scenario_request("a_customer_small_withdrawal", 1))
    assert again["check"]["path"] == "fast" and again["signer"]["signed"] is True
    assert again["check"]["path_reasons"][-1].endswith("AI check not needed")


def test_d_binance_is_refused_and_wallet_paused(api):
    c, _ = api
    out = withdraw(c, scenario_request("d_binance_7702"))
    chk = out["check"]
    assert chk["verdict"] == "BLOCK" and chk["path"] == "slow"
    assert {"R1", "R2", "R3"} <= {f["rule_id"] for f in chk["findings"]}
    assert out["approval"] is None
    assert out["signer"]["signed"] is False and "no approval" in out["signer"]["reason"]
    assert HOT_D in paused(c)
    # the paused wallet's next request is blocked early (latched), still not signed
    nxt = withdraw(c, {**scenario_request("d_binance_7702"), "authorization_list": None,
                       "to": "0xc000000000000000000000000000000000000d01", "value": "1"})
    assert nxt["check"]["path"] == "early" and nxt["signer"]["signed"] is False


def test_f_honest_large_withdrawal_needs_human_not_paused(api):
    c, _ = api
    out = withdraw(c, scenario_request("f_honest_large_withdrawal", 0))
    assert out["check"]["verdict"] == "NEEDS_HUMAN"
    assert [f["rule_id"] for f in out["check"]["findings"]] == ["R3"]
    assert out["signer"]["signed"] is False and out["approval"] is None
    assert HOT_F not in paused(c)
    nxt = withdraw(c, scenario_request("f_honest_large_withdrawal", 1))
    assert nxt["check"]["verdict"] == "ALLOW" and nxt["signer"]["signed"] is True


def test_reset_without_admin_token_is_refused_and_with_it_unpauses(api):
    c, _ = api
    withdraw(c, scenario_request("d_binance_7702"))
    assert HOT_D in paused(c)
    assert c.post("/breaker/reset", json={"wallet": HOT_D}).status_code == 401
    assert c.post("/breaker/reset", json={"wallet": HOT_D}, headers={"X-Admin-Token": "nope"}).status_code == 403
    assert HOT_D in paused(c)
    r = c.post("/breaker/reset", json={"wallet": HOT_D}, headers={"X-Admin-Token": ADMIN})
    assert r.status_code == 200 and HOT_D not in paused(c)


def test_approval_cannot_be_reused_or_moved_to_another_request(tmp_path):
    c, svc = client(tmp_path, runner=FakeRunner())
    body = scenario_request("a_customer_small_withdrawal")
    tx = TxRequest.model_validate(body)
    ap = svc.approver.issue(tx, svc.guard.check(tx).verdict)  # what /withdraw would get
    tampered = TxRequest.model_validate({**body, "to": "0xbad0000000000000000000000000000000000e01"})
    assert "does not match" in svc.signer.sign(tampered, ap).reason
    assert svc.signer.sign(tx, ap).signed is True
    assert "already used" in svc.signer.sign(tx, ap).reason


def test_mercado_scenario_through_the_api(tmp_path):
    c, _ = client(tmp_path, runner=FakeRunner())
    out = c.post("/scenarios/e_mercado_multichain/run").json()
    got = [(r["check"]["verdict"], r["signer"]["signed"]) for r in out["results"]]
    assert got[:8] == [("ALLOW", True)] * 8
    assert got[8:10] == [("DELAY", False)] * 2  # held: no approval until Phase 3 release
    assert all(v == ("BLOCK", False) for v in got[10:20])
    assert got[20] == ("ALLOW", True)  # the other customer still gets paid
    frozen = out["breaker"]["frozen_destinations"]  # the run's own isolated breaker state
    assert [f["address"] for f in frozen] == ["0xbad0000000000000000000000000000000000e01"]
    assert c.get("/breaker").json()["frozen_destinations"] == []  # main state untouched
