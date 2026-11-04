"""API features the web UI needs: /status, client run_id, demo-run approvals, blast radius."""

import pytest

from tests.api_helpers import SCENARIOS, FakeRunner, client

pytestmark = pytest.mark.skipif(not SCENARIOS.exists(), reason="scenarios/ not available")


@pytest.fixture
def api(tmp_path):
    return client(tmp_path, runner=FakeRunner())


def test_status_for_the_header(api):
    c, svc = api
    s = c.get("/status").json()
    assert s == {**s, "sandbox_mode": "fresh", "llm": "MOCK", "breaker_level": 0, "redteam_public": False}
    svc.history.pause("0x" + "a1" * 20, "R1 BLOCK", now=1)
    assert c.get("/status").json()["breaker_level"] == 2
    svc.history.set_global_stop("test", ["a", "b"], now=1)
    assert c.get("/status").json()["breaker_level"] == 4


def test_client_chosen_run_id_lets_the_ui_subscribe_first(api):
    c, _ = api
    out = c.post("/scenarios/a_customer_small_withdrawal/run?run_id=uirun0001").json()
    assert out["run_id"] == "uirun0001"
    kinds = [e["type"] for e in c.get("/events?run_id=uirun0001").json()]
    assert kinds[0] == "run_started" and "verdict" in kinds and "signer" in kinds
    assert c.post("/scenarios/a_customer_small_withdrawal/run?run_id=uirun0001").status_code == 409
    for bad in ("short", "UPPERCASE123", "x" * 40, "has-dash-123"):
        assert c.post(f"/scenarios/a_customer_small_withdrawal/run?run_id={bad}").status_code == 422


def test_blast_radius_and_skipped_time_in_the_result(api):
    c, _ = api
    out = c.post("/scenarios/e_mercado_multichain/run").json()
    [br] = out["blast_radius"]
    assert br["address"] == "0xbad0000000000000000000000000000000000e01" and br["frozen_by"] == "R7"
    assert br["allowed_totals"]["native_units"] == "40.0001" and br["allowed_totals"]["token_types"] == 6
    assert br["held_totals"]["token_types"] == 2 and br["real_attack_chains"] == 10
    assert br["chains_reached"] == [1, 10, 137, 42161, 43114]
    assert out["delay_worker"]["skipped_minutes"] == 15
    assert c.post("/scenarios/a_customer_small_withdrawal/run").json()["blast_radius"] == []


def test_visitor_approves_in_their_own_demo_run_never_main(api):
    c, svc = api
    out = c.post("/scenarios/f_honest_large_withdrawal/run?run_id=uirun0002").json()
    [p] = out["pending"]
    assert c.get("/demo/uirun0002/pending").json()[0]["id"] == p["id"]
    r = c.post(f"/demo/uirun0002/pending/{p['id']}/approve").json()
    assert r["status"] == "approved" and r["signed"] is True and r["by"] == "demo-visitor"
    assert c.get("/demo/uirun0002/pending").json() == []
    assert c.post(f"/demo/uirun0002/pending/{p['id']}/approve").status_code == 409
    assert svc.history.held(None) == [] and c.get("/history").json() == []  # main state untouched
    assert c.get("/pending").json() == []


def test_demo_reject_and_unknown_runs(api):
    c, _ = api
    out = c.post("/scenarios/f_honest_large_withdrawal/run?run_id=uirun0003").json()
    pid = out["pending"][0]["id"]
    assert c.post(f"/demo/uirun0003/pending/{pid}/reject").json()["status"] == "rejected"
    assert c.get("/demo/nosuchrun1/pending").status_code == 404
    assert c.post(f"/demo/uirun0003/pending/{pid}/delete").status_code == 404


def test_demo_runs_are_bounded(api, monkeypatch):
    c, svc = api
    monkeypatch.setattr("app.api.DEMO_RUN_MAX", 2)
    for i in range(3):
        c.post(f"/scenarios/a_customer_small_withdrawal/run?run_id=bounded{i:04d}")
    assert list(svc.demo_runs) == ["bounded0001", "bounded0002"]


def test_policies_are_read_only_plain_words(api):
    c, _ = api
    p = c.get("/policies").json()
    assert [r["id"] for r in p["rules"]] == ["R0", "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "C1"]
    assert "25%" in p["rules"][3]["text"] and "10 ETH" in p["rules"][4]["text"]
    assert [b["level"] for b in p["breaker"]] == [1, 2, 3, 4]
    assert p["sandbox"]["network"] == "none" and p["hold_minutes"] == 15
    assert c.post("/policies").status_code == 405  # read only
