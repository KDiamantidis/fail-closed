"""NEEDS_HUMAN -> pending list -> human approve / reject (admin), with a re-check first."""

import pytest

from tests.api_helpers import ADMIN, SCENARIOS, FakeRunner, client, scenario_request

pytestmark = pytest.mark.skipif(not SCENARIOS.exists(), reason="scenarios/ not available")
H = {"X-Admin-Token": ADMIN}
HOT_F = "0x1000000000000000000000000000000000000f01"


@pytest.fixture
def api(tmp_path):
    return client(tmp_path, runner=FakeRunner())


def hold_large(c) -> str:
    out = c.post("/withdraw", json=scenario_request("f_honest_large_withdrawal", 0)).json()
    assert out["check"]["verdict"] == "NEEDS_HUMAN" and out["signer"]["signed"] is False
    return out["check"]["check_id"]


def test_needs_human_goes_to_the_pending_list(api):
    c, _ = api
    hid = hold_large(c)
    [p] = c.get("/pending").json()
    assert p["id"] == hid and p["kind"] == "needs_human" and p["status"] == "pending"
    assert p["rule_ids"] == ["R3"] and p["request"]["from"] == HOT_F


def test_approve_needs_admin_token(api):
    c, _ = api
    hid = hold_large(c)
    assert c.post(f"/pending/{hid}/approve").status_code == 401
    assert c.post(f"/pending/{hid}/approve", headers={"X-Admin-Token": "x"}).status_code == 403
    assert c.post(f"/pending/{hid}/reject").status_code == 401
    assert c.get("/pending").json()[0]["status"] == "pending"


def test_approve_rechecks_then_signs_and_logs_who(api):
    c, svc = api
    hid = hold_large(c)
    r = c.post(f"/pending/{hid}/approve", json={"actor": "ops-alice"}, headers=H).json()
    # the re-check must not count the held 40 ETH twice (R6 would see 80 ETH)
    assert r["status"] == "approved" and r["signed"] is True and r["by"] == "ops-alice"
    item = svc.history.get_held(hid)
    assert item["decided_by"] == "ops-alice" and item["decided_at"] and item["approval_id"] == r["approval_id"]
    assert c.get("/pending").json() == []
    again = c.post(f"/pending/{hid}/approve", headers=H)
    assert again.status_code == 409 and "already decided" in again.json()["detail"]


def test_new_signal_since_the_hold_blocks_instead(api):
    c, svc = api
    hid = hold_large(c)
    svc.history.pause(HOT_F, "R1 BLOCK (another request)", now=svc.guard.clock())
    r = c.post(f"/pending/{hid}/approve", headers=H).json()
    assert r["status"] == "blocked" and r["verdict"] == "BLOCK" and r["signed"] is False
    assert "F2" in r["reason"] and "new signal" in r["reason"]


def test_reject(api):
    c, svc = api
    hid = hold_large(c)
    r = c.post(f"/pending/{hid}/reject", json={"actor": "ops-bob"}, headers=H).json()
    assert r["status"] == "rejected" and r["signed"] is False
    assert svc.history.get_held(hid)["decided_by"] == "ops-bob"


def test_unknown_and_delay_holds_cannot_be_approved_by_hand(api):
    c, svc = api
    assert c.post("/pending/nope/approve", headers=H).status_code == 404
    svc.history.hold("d1", "delay", 1.0, scenario_request("a_customer_small_withdrawal"), ["R7"], ["x"], 2.0)
    r = c.post("/pending/d1/approve", headers=H)
    assert r.status_code == 409 and "DELAY worker" in r.json()["detail"]


def test_human_approval_events(api):
    c, _ = api
    hid = hold_large(c)
    c.post(f"/pending/{hid}/approve", json={"actor": "ops-alice"}, headers=H)
    kinds = [e["type"] for e in c.get(f"/events?check_id={hid}").json()]
    assert kinds[-3:] == ["human_decision", "approval", "signer"]
