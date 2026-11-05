"""The fast-path escalation policy: the AI is asked only for a destination this wallet never
paid before, or an amount above AI_CHECK_MIN_PCT (10%) of the wallet balance. At most
AI_CHECK_CONCURRENCY AI calls at once; no free slot within the 2 s budget -> full simulation.
Whatever happens, the AI can only add checking: never an ALLOW without the rules or a simulation.

Real LLMExplainer with a fake OpenAI client, so the real call path runs.
"""

import threading
import time
from types import SimpleNamespace

from app.config import Settings
from app.models import Decision, Path
from guard import Guard
from history import History
from llm import LLMExplainer
from tests.test_guard import ETH, NOW, FakeRunner, StaticState, req
from tests.test_llm_fail_closed import SECRET, FakeClient, raise_429

ANSWER = '{"intent_summary":"plain transfer","risk_score":5,"explanation":"ok","escalate":false}'


def no_concerns(**kw):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=ANSWER))], usage=None)


def guard(behaviour=no_concerns, **kw):
    # R6 (hourly outflow) raised so a large-but-legal amount can reach the fast path in these tests
    s = Settings(vultr_inference_api_key=SECRET, llm_mode="live", r6_outflow_limit_wei=10**6 * ETH, **kw)
    return Guard(s, History(), StaticState(), FakeRunner(), LLMExplainer(s, client=FakeClient(behaviour)),
                 clock=lambda: NOW)


def test_known_destination_small_amount_no_ai_call():
    g = guard()
    g.check(req(), now=NOW)  # first payout to this destination: the AI is asked
    calls = g.explainer.client.calls
    r = g.check(req(), now=NOW + 60)  # same wallet, same destination, 1 ETH of 1000 ETH
    assert g.explainer.client.calls == calls  # no AI call
    assert r.verdict.path is Path.FAST and r.verdict.verdict is Decision.ALLOW
    assert r.verdict.path_reasons[-1] == ("Known destination, small amount (at most 10% of the balance): "
                                          "AI check not needed")
    assert r.llm is None and "llm" not in r.timings


def test_new_destination_asks_the_ai():
    g = guard()
    r = g.check(req())
    assert g.explainer.client.calls == 1
    assert "New destination for this wallet: AI check asked" in r.verdict.path_reasons
    assert r.verdict.path is Path.FAST and r.verdict.path_reasons[-1] == "LLM: no escalation"


def test_large_amount_to_a_known_destination_asks_the_ai():
    g = guard()
    g.check(req(), now=NOW)
    calls = g.explainer.client.calls
    r = g.check(req(value=150 * ETH), now=NOW + 60)  # 15% of 1000 ETH: above 10%, below R3's 25%
    assert g.explainer.client.calls == calls + 1
    assert "Known destination, but amount above 10% of the wallet balance: AI check asked" in r.verdict.path_reasons
    assert not r.verdict.findings or all(f.action is Decision.ALLOW for f in r.verdict.findings)


def test_threshold_is_a_policy_value():
    g = guard(ai_check_min_pct=20.0)
    g.check(req(), now=NOW)
    calls = g.explainer.client.calls
    r = g.check(req(value=150 * ETH), now=NOW + 60)  # 15% is under a 20% policy
    assert g.explainer.client.calls == calls
    assert "at most 20% of the balance" in r.verdict.path_reasons[-1]


def test_unknown_balance_counts_as_large():
    from state_provider import StartingState

    g = guard()
    g.state_provider = StaticState(StartingState.model_validate({"accounts": {}}))
    ask, why = g._ai_check_needed(req(), __import__("decode").decode(req(), []), None, NOW, None)
    assert ask and "wallet balance unknown" in why


def test_ai_answer_429_on_the_policy_path_is_still_full_simulation():
    g = guard(raise_429)
    r = g.check(req())
    assert r.verdict.path is Path.SLOW and g.runner.calls == 1
    assert r.verdict.path_reasons[-1].startswith("AI check unavailable (rate limited (429)) -> full simulation")


def test_cap_reached_goes_to_full_simulation_never_fast_allow():
    release = threading.Event()
    models: list[str] = []

    def blocked(**kw):
        models.append(kw["model"])
        if kw["model"] != "glm-5.3-flash":  # the background explanation of the slow-path check
            return no_concerns()
        release.wait(5)
        return no_concerns()

    g = guard(blocked, ai_check_concurrency=1, fast_path_llm_timeout_seconds=0.3)
    first = threading.Thread(target=g.check, args=(req(),))
    first.start()
    deadline = time.monotonic() + 3
    while g.explainer.client.calls < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    try:
        r = g.check(req(to="0x" + "c2" * 20))  # a second new destination while the only slot is taken
        assert r.verdict.path is Path.SLOW and g.runner.calls >= 1  # full simulation, not the fast path
        assert r.verdict.path_reasons[-1].startswith(
            "AI check unavailable (busy, 1 AI checks already running) -> full simulation")
        assert models.count("glm-5.3-flash") == 1  # no second fast-path AI call: the cap held
    finally:
        release.set()
        first.join(5)


def test_cap_waits_within_the_budget_for_a_free_slot():
    def quick(**kw):
        time.sleep(0.2)
        return no_concerns()

    g = guard(quick, ai_check_concurrency=1, fast_path_llm_timeout_seconds=2.0)
    out: list = []
    threads = [threading.Thread(target=lambda i=i: out.append(g.check(req(to="0x" + f"{i:02x}" * 20))))
               for i in range(1, 4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(out) == 3 and all(r.verdict.path is Path.FAST for r in out)  # each waited its turn
    assert g.explainer.client.calls == 3


def test_policy_off_switch_still_wins():
    g = guard(fast_path_llm_escalation=False)
    r = g.check(req())
    assert g.explainer.client.calls == 0 and r.verdict.path_reasons[-1] == "LLM escalation off"


# ---- the background explanation: the bigger model, then the fast one -------------------------

def test_explanation_uses_the_big_model_with_its_own_timeout():
    seen = []

    def record(**kw):
        seen.append((kw["model"], kw.get("timeout")))
        return no_concerns()

    s = Settings(vultr_inference_api_key=SECRET, llm_mode="live")
    e = LLMExplainer(s, client=FakeClient(record))
    a = e.explain(req(), None)
    assert seen == [("glm-5.3", 20.0)] and a.model == "glm-5.3"
    b = e.assess(req(), None)  # the fast-path check stays on the fast model, no per-call timeout override
    assert seen[-1] == ("glm-5.3-flash", None) and b.model == "glm-5.3-flash"


def test_explanation_falls_back_to_the_fast_model():
    def big_fails(**kw):
        if kw["model"] == "glm-5.3":
            return raise_429()
        return no_concerns()

    s = Settings(vultr_inference_api_key=SECRET, llm_mode="live")
    e = LLMExplainer(s, client=FakeClient(big_fails))
    a = e.explain(req(), None)
    assert a is not None and a.model == "glm-5.3-flash"
    assert e.usage.snapshot()["calls_by_model"] == {"glm-5.3": 1, "glm-5.3-flash": 1}


def test_explanation_event_names_the_model_that_wrote_it():
    events = []
    s = Settings(vultr_inference_api_key=SECRET, llm_mode="live")
    g = Guard(s, History(), StaticState(), FakeRunner(), LLMExplainer(s, client=FakeClient(no_concerns)),
              clock=lambda: NOW, events=lambda cid, k, d: events.append((k, d)))
    r = g.check(req(data="0x12345678"))  # not a plain transfer: slow path, explanation in the background
    r.explanation.result(timeout=5)
    ex = [d for k, d in events if k == "explanation"][0]
    assert ex["model"] == "glm-5.3" and ex["available"] is True
