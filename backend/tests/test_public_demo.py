"""Public demo mode (PUBLIC_DEMO=true on the VM): what visitors can do, what stays admin-only,
per-IP rate limits, "Reset demo" and the idle auto-reset, and the daily model budget."""

import json
import time
from fractions import Fraction
from types import SimpleNamespace

import pytest

import app.api as api
from app.config import Settings
from app.models import Path
from guard import Guard
from history import History
from llm import BUDGET_KIND, BUDGET_MESSAGE, DailyBudget, LLMExplainer, LLMUnavailable
from tests.api_helpers import ADMIN, FakeRunner, client, scenario_request
from tests.test_guard import CUST, ETH, HOT, NOW, FakeRunner as GuardRunner, StaticState, req
from tests.test_llm_fail_closed import SECRET, FakeClient

TX = scenario_request("a_customer_small_withdrawal")


def public(tmp_path, **kw):
    c, svc = client(tmp_path, runner=FakeRunner(), public_demo=True, **kw)
    svc.loadtest_runner = FakeRunner()
    return c, svc


# ---- admin-only on the Guard's own state ------------------------------------------------------

@pytest.mark.parametrize("path", ["/check", "/withdraw"])
def test_raw_check_and_withdraw_need_the_admin_token_in_public_mode(tmp_path, path):
    c, _ = public(tmp_path)
    assert c.post(path, json=TX).status_code == 401
    assert c.post(path, json=TX, headers={"X-Admin-Token": "wrong"}).status_code == 403
    assert c.post(path, json=TX, headers={"X-Admin-Token": ADMIN}).status_code == 200


def test_outside_public_mode_check_stays_open(tmp_path):
    c, _ = client(tmp_path, runner=FakeRunner())
    assert c.post("/check", json=TX).status_code == 200


def test_real_breaker_and_queue_actions_stay_admin_only(tmp_path):
    c, _ = public(tmp_path)
    run = c.post("/scenarios/g_coordinated_breaker/run?run_id=publicg0001").json()
    assert run["breaker"]["global_stop"]
    assert c.post("/breaker/reset-all").status_code == 401
    assert c.post("/breaker/reset", json={"global_stop": True}).status_code == 401
    assert c.post("/demo/publicg0001/breaker/reset-all").status_code == 401  # the "real" lift
    assert c.post("/pending/x/approve").status_code == 401
    # policies are read-only: there is no endpoint that changes them
    assert not [r for r in c.app.routes if "polic" in getattr(r, "path", "") and "POST" in getattr(r, "methods", ())]


def test_visitors_can_use_the_demo(tmp_path):
    c, _ = public(tmp_path)
    for name in ("d_delegation_drain", "e_multichain_drain", "f_honest_large_withdrawal", "g_coordinated_breaker"):
        assert c.post(f"/scenarios/{name}/run").status_code == 200
    f = c.post("/scenarios/f_honest_large_withdrawal/run?run_id=publicf0001").json()
    hold = f["pending"][0]["id"]
    assert c.post(f"/demo/publicf0001/pending/{hold}/approve").json()["status"] == "approved"
    with c.stream("POST", "/loadtest/run?normal=10") as r:
        assert r.status_code == 200 and "loadtest_finished" in "".join(r.iter_text())
    assert c.get("/policies").status_code == 200 and c.get("/redteam/last").status_code in (200, 404)
    s = c.get("/status").json()
    assert s["public_demo"] is True and s["demo_idle_reset_minutes"] == 10
    assert s["llm_budget"]["limit_usd"] == 1.0 and s["llm_budget"]["reached"] is False


# ---- rate limits --------------------------------------------------------------------------------

def test_replays_are_rate_limited_per_ip_with_a_plain_message(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "TRUSTED_PROXIES", ("testclient",))  # the test client acts as our proxy
    c, _ = public(tmp_path, rate_replays_per_minute=3)
    a, b = {"X-Forwarded-For": "198.51.100.7"}, {"X-Forwarded-For": "203.0.113.9"}
    for _ in range(3):
        assert c.post("/scenarios/a_customer_small_withdrawal/run", headers=a).status_code == 200
    r = c.post("/scenarios/a_customer_small_withdrawal/run", headers=a)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    assert r.json()["detail"].startswith("Too many replays from your address: at most 3 per minute.")
    assert c.post("/scenarios/a_customer_small_withdrawal/run", headers=b).status_code == 200  # another visitor


def test_simulations_are_rate_limited(tmp_path):
    c, _ = public(tmp_path)  # default: 2 per 10 minutes
    for _ in range(2):
        with c.stream("POST", "/loadtest/run?normal=10") as r:
            assert r.status_code == 200
            "".join(r.iter_text())
    r = c.post("/loadtest/run?normal=10")
    assert r.status_code == 429 and "at most 2 per 10 minutes" in r.json()["detail"]


def test_no_rate_limit_outside_public_mode(tmp_path):
    c, _ = client(tmp_path, runner=FakeRunner(), rate_replays_per_minute=1)
    for _ in range(3):
        assert c.post("/scenarios/a_customer_small_withdrawal/run").status_code == 200


def test_client_ip_trusts_forwarded_for_only_from_our_own_proxy():
    def request(peer, fwd=None):
        return SimpleNamespace(client=SimpleNamespace(host=peer),
                               headers={"x-forwarded-for": fwd} if fwd else {})

    assert api.client_ip(request("127.0.0.1", "10.9.9.9, 198.51.100.7")) == "198.51.100.7"  # the hop Caddy added
    assert api.client_ip(request("198.51.100.7", "1.2.3.4")) == "198.51.100.7"  # spoofed header ignored
    assert api.client_ip(request("127.0.0.1")) == "127.0.0.1"


# ---- Reset demo and the idle auto-reset -------------------------------------------------------

def test_reset_demo_drops_only_the_visitors_runs_back_to_level_0(tmp_path):
    c, _ = public(tmp_path)
    mine = c.post("/scenarios/g_coordinated_breaker/run?run_id=mineg000001").json()
    other = c.post("/scenarios/f_honest_large_withdrawal/run?run_id=otherf00001").json()
    assert mine["breaker"]["global_stop"] and other["pending"]
    r = c.post("/demo/reset", json={"run_ids": ["mineg000001", "unknown0000x"]}).json()
    assert r == {"reset": 1, "level": 0}
    gone = c.get("/demo/mineg000001/pending")
    assert gone.status_code == 404 and "Run the replay again" in gone.json()["detail"]
    assert c.get("/demo/otherf00001/pending").status_code == 200  # another visitor is untouched
    assert c.get("/status").json()["breaker_level"] == 0  # the Guard's own state was never at risk


def test_idle_demo_runs_reset_on_their_own(tmp_path):
    c, svc = public(tmp_path, demo_idle_reset_minutes=0.005)  # 0.3 s
    c.post("/scenarios/f_honest_large_withdrawal/run?run_id=idlef000001")
    for _ in range(3):  # activity keeps it alive
        time.sleep(0.15)
        assert c.get("/demo/idlef000001/pending").status_code == 200
    time.sleep(0.4)
    assert c.get("/demo/idlef000001/pending").status_code == 404
    assert svc.demo_ttl() == pytest.approx(0.3)


# ---- daily model budget ------------------------------------------------------------------------

def test_budget_counts_per_day_and_survives_a_restart(tmp_path):
    day = ["2026-11-07"]
    f = tmp_path / "budget.json"
    b = DailyBudget(1.0, str(f), today=lambda: day[0])
    b.add(Fraction("0.6"))
    assert not b.reached()
    b.add(Fraction("0.4"))
    assert b.reached()
    again = DailyBudget(1.0, str(f), today=lambda: day[0])  # a restart does not reset it
    assert again.reached() and json.loads(f.read_text())["day"] == "2026-11-07"
    day[0] = "2026-11-08"
    assert not again.reached() and again.snapshot()["spent_today_usd"] == 0
    assert not DailyBudget(0, today=lambda: day[0]).reached()  # 0 = no cap


def usage_answer(prompt=1_000_000, completion=0):
    answer = '{"intent_summary":"x","risk_score":5,"explanation":"ok","escalate":false}'
    return lambda **kw: SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=answer))],
                                        usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion))


def test_budget_reached_pauses_the_model_and_never_uses_the_mock():
    s = Settings(vultr_inference_api_key=SECRET, llm_mode="live", llm_daily_budget_usd=0.1)
    e = LLMExplainer(s, client=FakeClient(usage_answer()))  # 1M prompt tokens of glm-5.3-flash = $0.10
    assert e.assess_or_raise(req(), None).mock is False
    calls = e.client.calls
    with pytest.raises(LLMUnavailable) as err:
        e.assess_or_raise(req(), None)
    assert err.value.kind == BUDGET_KIND and e.client.calls == calls  # no call was made
    assert e.explain(req(), None) is None  # explanations pause too (no mock instead)
    assert e.usage.snapshot()["failures"] == {}


def test_decisions_keep_working_when_the_budget_is_reached():
    events = []
    s = Settings(vultr_inference_api_key=SECRET, llm_mode="live", llm_daily_budget_usd=0.001,
                 r6_outflow_limit_wei=10**6 * ETH)
    e = LLMExplainer(s, client=FakeClient(usage_answer()))
    e.budget.add(Fraction(1))  # already spent today
    g = Guard(s, History(), StaticState(), GuardRunner(), e, clock=lambda: NOW,
              events=lambda cid, k, d: events.append((k, d)))
    g.history.seed_payout(HOT, CUST, 1, NOW - 7 * 86400, "seed")
    r = g.check(req(value=150 * ETH))  # a large amount: the AI check would be asked
    assert r.verdict.path is Path.SLOW and r.verdict.verdict.value == "ALLOW"  # decided by the simulation
    assert r.verdict.path_reasons[-1].startswith("AI check unavailable (demo budget reached) -> full simulation")
    r.explanation.result(timeout=5)
    ex = [d for k, d in events if k == "explanation"][0]
    assert ex["available"] is False and ex["budget"] is True and ex["reason"] == BUDGET_MESSAGE
    assert BUDGET_MESSAGE == "Demo budget reached: AI explanations paused until tomorrow"
    assert e.client.calls == 0
