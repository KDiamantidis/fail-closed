"""The fast-path escalation policy: a destination this wallet never paid before goes straight to
full simulation (no AI check first). For a known destination the AI is asked only about an amount
above AI_CHECK_MIN_PCT (10%) of the wallet balance. LLM_CONCURRENCY model calls at once, shared by
fast-path checks and explanations, checks first; no free slot within the 2 s budget -> full
simulation. Whatever happens, the AI can only add checking: never an ALLOW without the rules or a
simulation.

Real LLMExplainer with a fake OpenAI client, so the real call path runs.
"""

import threading
import time
from types import SimpleNamespace

from app.config import Settings
from app.models import Decision, Path
from guard import Guard
from history import History
from llm import CallSlots, LLMExplainer
from tests.test_guard import CUST, ETH, HOT, NOW, FakeRunner, StaticState, req
from tests.test_llm_fail_closed import SECRET, FakeClient, raise_429

ANSWER = '{"intent_summary":"plain transfer","risk_score":5,"explanation":"ok","escalate":false}'
BIG = 150 * ETH  # 15% of 1000 ETH: above 10%, below R3's 25%
KNOWN = [CUST] + ["0x" + f"{i:02x}" * 20 for i in range(1, 4)]


def no_concerns(**kw):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=ANSWER))], usage=None)


def guard(behaviour=no_concerns, **kw):
    # R6 (hourly outflow) raised so a large-but-legal amount can reach the fast path in these tests
    s = Settings(vultr_inference_api_key=SECRET, llm_mode="live", r6_outflow_limit_wei=10**6 * ETH, **kw)
    g = Guard(s, History(), StaticState(), FakeRunner(), LLMExplainer(s, client=FakeClient(behaviour)),
              clock=lambda: NOW)
    for to in KNOWN:  # destinations this wallet paid a week ago
        g.history.seed_payout(HOT, to, 1, NOW - 7 * 86400, f"seed-{to[-4:]}")
    return g


def model_calls(g, model="glm-5.3-flash"):
    return g.explainer.usage.snapshot()["calls_by_model"].get(model, 0)


def test_known_destination_small_amount_no_ai_call():
    g = guard()
    r = g.check(req())  # known destination, 1 ETH of 1000 ETH
    assert g.explainer.client.calls == 0  # no AI call
    assert r.verdict.path is Path.FAST and r.verdict.verdict is Decision.ALLOW
    assert r.verdict.path_reasons[-1] == ("Known destination, small amount (at most 10% of the balance): "
                                          "AI check not needed")
    assert r.llm is None and "llm" not in r.timings


def test_new_destination_goes_straight_to_full_simulation():
    g = guard()
    r = g.check(req(to="0x" + "d7" * 20))
    assert r.verdict.path is Path.SLOW and g.runner.calls == 1
    assert r.verdict.verdict is Decision.ALLOW
    assert r.verdict.path_reasons[-1] == "New destination: full simulation"
    assert model_calls(g) == 0 and "llm" not in r.timings  # the AI was not asked first
    r.explanation.result(timeout=5)  # ... but it still writes the explanation afterwards
    assert model_calls(g, "glm-5.3") == 1


def test_large_amount_to_a_known_destination_asks_the_ai():
    g = guard()
    r = g.check(req(value=BIG))
    assert g.explainer.client.calls == 1
    assert "Known destination, amount above 10% of the wallet balance: AI check asked" in r.verdict.path_reasons
    assert r.verdict.path is Path.FAST and r.verdict.path_reasons[-1] == "LLM: no escalation"


def test_threshold_is_a_policy_value():
    g = guard(ai_check_min_pct=20.0)
    r = g.check(req(value=BIG))  # 15% is under a 20% policy
    assert g.explainer.client.calls == 0
    assert "at most 20% of the balance" in r.verdict.path_reasons[-1]


def test_unknown_balance_counts_as_large():
    from decode import decode

    g = guard()
    action, why = g._ai_check_needed(req(), decode(req(), []), None, NOW, None)
    assert action == "ask" and why == "Known destination, wallet balance unknown: AI check asked"


def test_ai_answer_429_on_the_policy_path_is_still_full_simulation():
    g = guard(raise_429)
    r = g.check(req(value=BIG))
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

    g = guard(blocked, llm_concurrency=1, fast_path_llm_timeout_seconds=0.3)
    first = threading.Thread(target=g.check, args=(req(value=BIG),))
    first.start()
    deadline = time.monotonic() + 3
    while g.explainer.client.calls < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    try:
        r = g.check(req(to=KNOWN[1], value=BIG))  # a second large amount while the only slot is taken
        assert r.verdict.path is Path.SLOW and g.runner.calls >= 1  # full simulation, not the fast path
        assert r.verdict.path_reasons[-1].startswith(
            "AI check unavailable (busy, 1 AI calls already running) -> full simulation")
        assert models.count("glm-5.3-flash") == 1  # no second fast-path AI call: the cap held
    finally:
        release.set()
        first.join(5)


def test_cap_waits_within_the_budget_for_a_free_slot():
    def quick(**kw):
        time.sleep(0.2)
        return no_concerns()

    g = guard(quick, llm_concurrency=1, fast_path_llm_timeout_seconds=2.0)
    out: list = []
    threads = [threading.Thread(target=lambda to=to: out.append(g.check(req(to=to, value=BIG))))
               for to in KNOWN[1:]]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(out) == 3 and all(r.verdict.path is Path.FAST for r in out)  # each waited its turn
    assert g.explainer.client.calls == 3


# ---- one shared limit: fast-path checks before explanations -----------------------------------

def test_one_shared_limit_fast_checks_before_explanations():
    """With 2 slots, explanations use at most 1. A fast-path check that arrives while the only
    other slot is taken gets the next free slot before an explanation that was already waiting."""
    slots = CallSlots(2)
    order: list[str] = []
    assert slots.acquire(fast=False) and slots.acquire(fast=True)  # 1 explanation + 1 check running
    waiting = threading.Thread(target=lambda: (slots.acquire(fast=False), order.append("explain")))
    waiting.start()
    time.sleep(0.05)
    fast = threading.Thread(target=lambda: order.append("fast" if slots.acquire(fast=True, timeout=2) else "none"))
    fast.start()
    time.sleep(0.05)
    slots.release(fast=True)  # the check's slot frees: the next check gets it, the explanation waits
    fast.join(2)
    time.sleep(0.05)
    assert order == ["fast"]
    slots.release(fast=False)  # the running explanation ends: the waiting one may start
    waiting.join(2)
    assert order == ["fast", "explain"] and slots.snapshot()["explain_waits"] == 1


def test_explanations_never_take_the_last_slot():
    """3 shared slots: at most 2 explanations at once, so a check always finds one free slot."""
    slots = CallSlots(3)
    assert slots.snapshot()["explain_max"] == 2
    assert slots.acquire(fast=False) and slots.acquire(fast=False)
    assert slots.acquire(fast=False, timeout=0.2) is False  # a third explanation waits
    t0 = time.monotonic()
    assert slots.acquire(fast=True, timeout=2) and time.monotonic() - t0 < 0.1  # the check goes at once
    snap = slots.snapshot()
    assert (snap["in_use"], snap["explaining"]) == (3, 2)
    for fast in (True, False, False):
        slots.release(fast=fast)
    assert slots.snapshot()["in_use"] == 0


def test_busy_explanations_leave_a_slot_for_the_large_amount_check():
    """The 2026-11-06 live run: 3 explanations held all 3 slots and a check missed its 2 s. Now
    two slow explanations run, and the check gets the third slot on the fast path."""
    release = threading.Event()

    def slow_explanations(**kw):
        if kw["model"] == "glm-5.3":
            release.wait(5)
        return no_concerns()

    g = guard(slow_explanations, llm_concurrency=3, fast_path_llm_timeout_seconds=1.0)
    first = [g.check(req(to="0x" + f"{0xe0 + i:02x}" * 20)) for i in range(3)]  # 3 new destinations
    try:
        deadline = time.monotonic() + 3
        while g.explainer.slots.snapshot()["explaining"] < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert g.explainer.slots.snapshot()["explaining"] == 2  # the third explanation waits in line
        r = g.check(req(value=BIG))
        assert r.verdict.path is Path.FAST and r.verdict.path_reasons[-1] == "LLM: no escalation"
    finally:
        release.set()
        for x in first:
            x.explanation.result(timeout=10)


def test_fast_check_without_a_slot_in_time_gives_up():
    slots = CallSlots(1)
    slots.acquire(fast=False)  # a long explanation holds the only slot
    t0 = time.monotonic()
    assert slots.acquire(fast=True, timeout=0.2) is False and time.monotonic() - t0 < 1
    slots.release()
    assert slots.acquire(fast=False, timeout=0.2)  # a check that gave up does not block explanations


def test_explanations_and_checks_share_the_limit():
    """With 1 slot, a running explanation makes a large-amount check wait; past its budget the
    check goes to full simulation (never ALLOW on the fast path)."""
    release = threading.Event()

    def slow_explanation(**kw):
        if kw["model"] == "glm-5.3":
            release.wait(5)
        return no_concerns()

    g = guard(slow_explanation, llm_concurrency=1, fast_path_llm_timeout_seconds=0.3)
    first = g.check(req(to="0x" + "d8" * 20))  # new destination: simulation, explanation in the background
    try:
        deadline = time.monotonic() + 3
        while g.explainer.slots.snapshot()["in_use"] < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        r = g.check(req(value=BIG))
        assert r.verdict.path is Path.SLOW
        assert r.verdict.path_reasons[-1].startswith("AI check unavailable (busy, 1 AI calls already running)")
    finally:
        release.set()
        first.explanation.result(timeout=5)


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
    assert e.slots.snapshot()["in_use"] == 0  # both slots given back


def test_explanation_event_names_the_model_that_wrote_it():
    events = []
    s = Settings(vultr_inference_api_key=SECRET, llm_mode="live")
    g = Guard(s, History(), StaticState(), FakeRunner(), LLMExplainer(s, client=FakeClient(no_concerns)),
              clock=lambda: NOW, events=lambda cid, k, d: events.append((k, d)))
    r = g.check(req(data="0x12345678"))  # not a plain transfer: slow path, explanation in the background
    r.explanation.result(timeout=5)
    ex = [d for k, d in events if k == "explanation"][0]
    assert ex["model"] == "glm-5.3" and ex["available"] is True
