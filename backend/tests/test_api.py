"""API surface with a fake sandbox (no Docker needed)."""

import json

import pytest

from tests.api_helpers import ADMIN, SECRET_KEY, FakeRunner, client, scenario_request

pytestmark = pytest.mark.skipif(not __import__("tests.api_helpers", fromlist=["SCENARIOS"]).SCENARIOS.exists(),
                                reason="scenarios/ not available")


@pytest.fixture
def api(tmp_path):
    c, svc = client(tmp_path, runner=FakeRunner())
    return c, svc


def test_health_and_public_key(api):
    c, svc = api
    assert c.get("/health").json()["status"] == "ok"
    pk = c.get("/guard/public-key").json()
    assert pk["public_key"] == svc.approver.public_key_hex and pk["algorithm"] == "ed25519"


def test_check_allow_includes_approval_and_details(api):
    c, _ = api
    r = c.post("/check", json=scenario_request("a_customer_small_withdrawal")).json()
    assert r["verdict"] == "ALLOW" and r["path"] == "fast"
    assert r["approval"]["verdict"] == "ALLOW" and r["approval"]["signature"]
    assert r["path_reasons"] and "total" in r["timings_ms"] and r["llm"]["mock"] is True


def test_check_non_allow_has_no_approval(api):
    c, _ = api
    r = c.post("/check", json=scenario_request("c_treasury_unknown_above_limit")).json()
    assert r["verdict"] == "BLOCK" and r["approval"] is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.pop("wallet_type"),
        lambda b: b.update(wallet_type="cold"),
        lambda b: b.update(to="0x1234"),
        lambda b: b.update(value=-1),
        lambda b: b.update(chain_id=0),
    ],
)
def test_bad_input_is_422(api, mutate):
    c, _ = api
    body = scenario_request("a_customer_small_withdrawal")
    mutate(body)
    assert c.post("/check", json=body).status_code == 422
    assert c.post("/withdraw", json=body).status_code == 422


def test_history_newest_first_and_breaker(api):
    c, _ = api
    c.post("/check", json=scenario_request("a_customer_small_withdrawal"))
    c.post("/check", json=scenario_request("c_treasury_unknown_above_limit"))
    h = c.get("/history?limit=10").json()
    assert [x["verdict"] for x in h] == ["BLOCK", "ALLOW"]
    assert c.get("/history?limit=1").json()[0]["verdict"] == "BLOCK"
    b = c.get("/breaker").json()
    assert b["frozen_destinations"] == [] and b["paused_wallets"] == []


def test_events_cover_every_step_of_a_withdrawal(api):
    c, _ = api
    out = c.post("/withdraw", json=scenario_request("b_treasury_allowlisted_above_limit")).json()
    cid = out["check"]["check_id"]
    kinds = [e["type"] for e in c.get(f"/events?check_id={cid}").json()]
    assert kinds == ["received", "decoded", "request_rules", "path", "sandbox_started", "sandbox_done",
                     "findings", "verdict", "approval", "signer"]


def test_stream_is_server_sent_events(api):
    c, _ = api
    c.post("/withdraw", json=scenario_request("a_customer_small_withdrawal"))
    with c.stream("GET", "/stream?replay=3&limit=3") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    assert [e["type"] for e in events] == ["verdict", "approval", "signer"]
    assert "event: signer" in body and "id: " in body


def test_reset_needs_admin_token(api):
    c, svc = api
    svc.history.freeze("0x" + "ba" * 20, "R8: test", now=1)
    body = {"address": "0x" + "ba" * 20}
    assert c.post("/breaker/reset", json=body).status_code == 401
    assert c.post("/breaker/reset", json=body, headers={"X-Admin-Token": "wrong"}).status_code == 403
    assert c.post("/breaker/reset", json={"address": "0x1", "wallet": "0x2"},
                  headers={"X-Admin-Token": ADMIN}).status_code == 422
    r = c.post("/breaker/reset", json=body, headers={"X-Admin-Token": ADMIN})
    assert r.status_code == 200 and r.json()["reset"] is True
    assert svc.history.events()[-1]["actor"] == "api-admin"


def test_reset_disabled_without_configured_token(tmp_path):
    c, _ = client(tmp_path, runner=FakeRunner(), admin_token="")
    r = c.post("/breaker/reset", json={"wallet": "0x" + "a1" * 20}, headers={"X-Admin-Token": ""})
    assert r.status_code in (401, 403)
    assert c.post("/breaker/reset", json={"wallet": "0x" + "a1" * 20},
                  headers={"X-Admin-Token": "anything"}).status_code == 403


def test_scenarios_list_and_unknown_name(api):
    c, _ = api
    names = [s["name"] for s in c.get("/scenarios").json()]
    assert names[0] == "a_customer_small_withdrawal" and "d_binance_7702" in names
    assert c.post("/scenarios/..%2F..%2Fetc%2Fpasswd/run").status_code == 404
    assert c.post("/scenarios/nope/run").status_code == 404


def test_cors_only_for_local_frontend(api):
    c, _ = api
    ok = c.options("/history", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = c.options("/history", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in bad.headers


def test_no_secret_in_any_response(api, tmp_path):
    c, svc = api
    pem = (tmp_path / "keys" / "guard.pem").read_text()
    private_marker = pem.splitlines()[1]  # base64 body of the private key
    bodies = [
        c.get("/health").text, c.get("/guard/public-key").text,
        c.post("/check", json=scenario_request("a_customer_small_withdrawal")).text,
        c.post("/withdraw", json=scenario_request("b_treasury_allowlisted_above_limit")).text,
        c.get("/history").text, c.get("/breaker").text, c.get("/events").text, c.get("/scenarios").text,
    ]
    for b in bodies:
        assert SECRET_KEY not in b and ADMIN not in b and private_marker not in b and "PRIVATE KEY" not in b
