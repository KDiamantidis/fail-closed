"""Security audit, phase 2: one test (or more) per finding that was fixed (F2-F5, F8-F12, F15-F17).
F1 and F6 are in test_concurrency.py. Visitors are simulated as cloudflared does it: requests from
this host with a CF-Connecting-IP header."""

from types import SimpleNamespace

import pytest

import app.api as api
from app.config import Settings
from llm import SHARE_KIND, LLMExplainer, LLMUnavailable, current_visitor
from tests.api_helpers import ADMIN, FakeRunner, client
from tests.test_guard import req
from tests.test_llm_fail_closed import SECRET, FakeClient

A = {"CF-Connecting-IP": "198.51.100.10"}
B = {"CF-Connecting-IP": "203.0.113.20"}


@pytest.fixture
def demo(tmp_path, monkeypatch):
    """Public demo behind cloudflared; the test client stands in for 127.0.0.1."""
    monkeypatch.setattr(api, "TRUSTED_PROXIES", ("testclient",))

    def make(**kw):
        c, svc = client(tmp_path, runner=FakeRunner(), public_demo=True, trusted_proxy="cloudflared", **kw)
        svc.loadtest_runner = FakeRunner()
        return c, svc
    return make


# ---- F2: a visitor acts only on their own runs; the Guard's own events are admin only ------------

def test_f2_events_and_runs_are_private_to_their_visitor(demo):
    c, _ = demo()
    run = c.post("/scenarios/f_honest_large_withdrawal/run?run_id=victim0000aa", headers=A).json()
    hold = run["pending"][0]["id"]
    assert c.get("/events?after=0", headers=B).status_code == 404  # no run_id: no longer everyone's events
    assert c.get("/events?after=0&run_id=main", headers=B).status_code == 404  # the Guard's own: admin
    assert c.get("/events?after=0&run_id=victim0000aa", headers=B).status_code == 404  # not B's run
    assert c.get("/events?after=0&run_id=victim0000aa", headers=A).status_code == 200  # A's own
    assert c.post(f"/demo/victim0000aa/pending/{hold}/approve", headers=B).status_code == 404
    assert c.post("/demo/reset", json={"run_ids": ["victim0000aa"]}, headers=B).json()["reset"] == 0
    assert c.get("/demo/victim0000aa/pending", headers=A).status_code == 200  # A's demo is untouched
    assert c.get("/events?after=0", headers={**B, "X-Admin-Token": ADMIN}).status_code == 200  # the operator


def test_f2_stream_needs_your_own_run(demo):
    c, _ = demo()
    assert c.get("/stream?limit=1", headers=A).status_code == 404
    assert c.get("/stream?limit=1&run_id=main", headers=A).status_code == 404


# ---- F3: IPv6 /64, IPv4-mapped, a global cap -------------------------------------------------------

def test_f3_visitor_key_groups_ipv6_by_64_and_maps_ipv4():
    assert api.visitor_key("2001:db8:1:2::1") == api.visitor_key("2001:db8:1:2:ffff::9") == "2001:db8:1:2::/64"
    assert api.visitor_key("::ffff:198.51.100.7") == "198.51.100.7"
    assert api.visitor_key("2001:db8:1:3::1") != api.visitor_key("2001:db8:1:2::1")


def test_f3_one_ipv6_network_is_one_visitor(demo):
    c, _ = demo(rate_replays_per_minute=2)
    for n in (1, 2):
        assert c.post("/scenarios/a_customer_small_withdrawal/run",
                      headers={"CF-Connecting-IP": f"2001:db8:1:2::{n}"}).status_code == 200
    assert c.post("/scenarios/a_customer_small_withdrawal/run",
                  headers={"CF-Connecting-IP": "2001:db8:1:2::99"}).status_code == 429
    assert c.post("/scenarios/a_customer_small_withdrawal/run",
                  headers={"CF-Connecting-IP": "::ffff:198.51.100.10"}).status_code == 200  # = A, first one


def test_f3_global_cap_for_all_visitors(demo):
    c, _ = demo(rate_global_replays_per_minute=3)
    codes = [c.post("/scenarios/a_customer_small_withdrawal/run", headers={"CF-Connecting-IP": f"198.51.100.{i}"})
             for i in range(1, 5)]
    assert [r.status_code for r in codes] == [200, 200, 200, 429]
    assert "from all visitors" in codes[-1].json()["detail"]


# ---- F4: explanations cached by request; a visitor's share of the AI budget -------------------------

def test_f4_the_same_step_is_explained_once():
    calls = []
    answer = '{"intent_summary":"x","risk_score":5,"explanation":"ok","escalate":false}'

    def model(**kw):
        calls.append(kw["model"])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=answer))], usage=None)

    e = LLMExplainer(Settings(vultr_inference_api_key=SECRET, llm_mode="live"), client=FakeClient(model))
    first, again = e.explain(req(), None), e.explain(req(), None)
    assert first.explanation == again.explanation and len(calls) == 1 and e.cache_hits == 1
    e.explain(req(value=2), None)  # another request: a new call
    assert len(calls) == 2
    e.assess_or_raise(req(), None)  # the fast AI check of the same step: also paid once
    e.assess_or_raise(req(), None)
    assert calls.count(e.settings.vultr_model) == 1


def test_f4_one_visitor_cannot_use_the_whole_budget():
    answer = '{"intent_summary":"x","risk_score":5,"explanation":"ok","escalate":false}'
    usage = SimpleNamespace(prompt_tokens=1_000_000, completion_tokens=0)  # $0.10 with glm-5.3-flash

    def model(**kw):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=answer))], usage=usage)

    s = Settings(vultr_inference_api_key=SECRET, llm_mode="live", llm_daily_budget_usd=1.0, llm_visitor_share_usd=0.1)
    e = LLMExplainer(s, client=FakeClient(model))
    token = current_visitor.set("198.51.100.10")
    try:
        e.assess_or_raise(req(), None)  # spends the visitor's whole share
        with pytest.raises(LLMUnavailable) as err:
            e.assess_or_raise(req(value=3), None)
        assert err.value.kind == SHARE_KIND
    finally:
        current_visitor.reset(token)
    token = current_visitor.set("203.0.113.20")  # someone else still gets explanations
    try:
        assert e.assess_or_raise(req(value=4), None) is not None
    finally:
        current_visitor.reset(token)


# ---- F5: stored demo runs per visitor -------------------------------------------------------------

def test_f5_a_flood_drops_only_the_flooders_own_runs(demo):
    c, _ = demo(demo_runs_per_visitor=3, rate_replays_per_minute=100)
    c.post("/scenarios/f_honest_large_withdrawal/run?run_id=victim0000bb", headers=A)
    for i in range(6):
        c.post(f"/scenarios/c_treasury_unknown_above_limit/run?run_id=flood{i:07d}",
               headers={"CF-Connecting-IP": "2001:db8:9:9::" + str(i + 1)})
    assert c.get("/demo/victim0000bb/pending", headers=A).status_code == 200  # still there
    flood = "2001:db8:9:9::1"
    assert c.get("/demo/flood0000000/pending", headers={"CF-Connecting-IP": flood}).status_code == 404  # oldest of theirs
    assert c.get("/demo/flood0000005/pending", headers={"CF-Connecting-IP": flood}).status_code == 200


# ---- F8, F9, F16: headers, no API docs, no CORS in public mode ----------------------------------------

def test_f8_security_headers_in_public_mode(demo, tmp_path):
    c, _ = demo()
    h = c.get("/status", headers=A).headers
    assert "frame-ancestors 'none'" in h["content-security-policy"] and h["x-frame-options"] == "DENY"
    assert h["x-content-type-options"] == "nosniff" and h["referrer-policy"] == "no-referrer"


def test_f9_no_api_docs_in_public_mode(demo, tmp_path):
    c, _ = demo()
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert c.get(path, headers=A).status_code == 404
    open_c, _ = client(tmp_path / "open", runner=FakeRunner())
    assert open_c.get("/openapi.json").status_code == 200  # local development keeps them


def test_f16_no_cors_origin_in_public_mode(demo):
    c, _ = demo()
    r = c.options("/check", headers={**A, "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in r.headers


# ---- F10, F11: body size, general cap, streams, reply size -------------------------------------------

def test_f10_large_and_chunked_bodies_are_refused(demo):
    c, _ = demo()
    r = c.post("/demo/reset", headers={**A, "Content-Type": "application/json"}, content=b"{" + b" " * 70_000 + b"}")
    assert r.status_code == 413
    r = c.post("/demo/reset", headers={**A, "Transfer-Encoding": "chunked", "Content-Type": "application/json"},
               content=iter([b'{"run_ids": []}']))
    assert r.status_code == 411


def test_f11_general_cap_per_visitor(demo):
    c, _ = demo(rate_requests_per_minute=5)
    codes = [c.get("/status", headers=A).status_code for _ in range(6)]
    assert codes == [200] * 5 + [429] and c.get("/status", headers=B).status_code == 200


def test_f11_open_streams_per_visitor(demo):
    c, svc = demo(stream_max_per_visitor=2)
    c.post("/scenarios/a_customer_small_withdrawal/run?run_id=streamrun01", headers=A)
    svc.streams[api.visitor_key("198.51.100.10")] = 2  # two already open
    r = c.get("/stream?run_id=streamrun01&limit=1", headers=A)
    assert r.status_code == 429 and "live-update connections" in r.json()["detail"]


def test_f11_events_reply_is_capped(demo):
    c, _ = demo(events_max_reply=5)
    c.post("/scenarios/e_multichain_drain/run?run_id=bigrun0001", headers=A)
    assert len(c.get("/events?after=0&run_id=bigrun0001", headers=A).json()) == 5


# ---- F12: wrong admin tokens -------------------------------------------------------------------------

def test_f12_too_many_wrong_admin_tokens(demo):
    c, _ = demo(admin_failures_per_10_minutes=3)
    for _ in range(3):
        assert c.post("/breaker/reset-all", headers={**A, "X-Admin-Token": "wrong"}).status_code == 403
    r = c.post("/breaker/reset-all", headers={**A, "X-Admin-Token": ADMIN})
    assert r.status_code == 429  # even the right token waits now
    assert c.post("/breaker/reset-all", headers={**B, "X-Admin-Token": ADMIN}).status_code == 200


# ---- F15: no visitor address behind cloudflared --------------------------------------------------------

def test_f15_cloudflared_requests_need_a_visitor_address(demo):
    c, _ = demo()
    r = c.get("/status", headers={"Host": "demo.trycloudflare.com"})
    assert r.status_code == 400 and "CF-Connecting-IP" in r.json()["detail"]
    assert c.get("/status", headers={"Host": "127.0.0.1:8000"}).status_code == 200  # the operator on this machine


# ---- F17: the Guard's own state is admin only ----------------------------------------------------------

@pytest.mark.parametrize("path", ["/history", "/pending", "/breaker"])
def test_f17_guard_state_is_admin_only(demo, path):
    c, _ = demo()
    assert c.get(path, headers=A).status_code == 401
    assert c.get(path, headers={**A, "X-Admin-Token": ADMIN}).status_code == 200
