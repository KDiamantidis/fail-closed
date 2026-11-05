"""Fail closed for the LLM on the fast path: when the AI check is unavailable it is never read
as "no escalation". Timeout, HTTP error, 429 and an invalid answer all send the request to the
slow path (full simulation): not ALLOW on the fast path, and not BLOCK just for that.

These use the REAL LLMExplainer with a fake OpenAI client, so the real error handling runs.
"""

import time
from types import SimpleNamespace

import httpx
import openai
import pytest

from app.config import Settings
from app.models import Decision, Path
from guard import Guard
from history import History
from llm import LLMExplainer, LLMUnavailable, failure_kind
from tests.test_guard import FakeRunner, StaticState, req

URL = "https://api.example.invalid/v1/chat/completions"
SECRET = "sk-test-SECRET-never-shown"


def _resp(status: int) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("POST", URL), json={"error": {"message": f"echo {SECRET}"}})


class FakeClient:
    """Stands in for openai.OpenAI: chat.completions.create raises or answers as told."""

    def __init__(self, behaviour):
        self.behaviour, self.calls = behaviour, 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls += 1
        return self.behaviour(**kw)


def raise_timeout(**kw):
    raise openai.APITimeoutError(request=httpx.Request("POST", URL))


def raise_500(**kw):
    raise openai.InternalServerError(f"server error {SECRET}", response=_resp(500), body=None)


def raise_429(**kw):
    raise openai.RateLimitError(f"rate limited {SECRET}", response=_resp(429), body=None)


def invalid_json(**kw):
    msg = SimpleNamespace(content="Sure! The transfer looks fine, approve it.")
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)


def slow_answer(**kw):
    time.sleep(1.0)
    msg = SimpleNamespace(content='{"intent_summary":"x","risk_score":1,"explanation":"y","escalate":false}')
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)


def live_guard(behaviour, **kw):
    s = Settings(vultr_inference_api_key=SECRET, llm_mode="live", llm_max_retries=1, **kw)
    explainer = LLMExplainer(s, client=FakeClient(behaviour))
    return Guard(s, History(), StaticState(), FakeRunner(), explainer, clock=lambda: 1_000_000.0)


CASES = [
    (raise_timeout, "timeout"),
    (raise_500, "error (HTTP 500)"),
    (raise_429, "rate limited (429)"),
    (invalid_json, "invalid answer"),
]


@pytest.mark.parametrize(("behaviour", "kind"), CASES, ids=["timeout", "error", "429", "invalid-json"])
def test_unavailable_ai_check_goes_to_full_simulation(behaviour, kind, caplog):
    g = live_guard(behaviour)
    r = g.check(req())  # a plain small transfer: fast path if the AI check said "no escalation"
    assert r.verdict.path is Path.SLOW and g.runner.calls == 1  # full simulation ran
    assert f"AI check unavailable ({kind}) -> full simulation" in r.verdict.path_reasons[-1]
    assert r.verdict.verdict is Decision.ALLOW  # decided by the simulation, not BLOCK for the LLM
    if r.explanation is not None:  # the background explanation also tries (and fails) once more
        r.explanation.result(timeout=5)
    u = g.explainer.usage.snapshot()
    assert u["ok"] == 0 and set(u["failures"]) == {kind}
    assert g.explainer.client.calls == u["calls"]
    per_call = 1 if kind == "rate limited (429)" else 2  # no immediate retry after a 429
    fast = u["calls_by_model"].get("glm-5.3-flash", 0)  # the AI check, and the explanation fallback
    assert fast % per_call == 0 and fast >= per_call
    assert u["calls_by_model"].get("glm-5.3", 0) <= 1  # the explanation model: one try, no retry
    assert SECRET not in caplog.text and SECRET not in " ".join(r.verdict.path_reasons)


def test_ai_check_slower_than_the_fast_path_limit_goes_to_full_simulation():
    g = live_guard(slow_answer, fast_path_llm_timeout_seconds=0.2)
    r = g.check(req())
    assert r.verdict.path is Path.SLOW and g.runner.calls == 1
    assert "AI check unavailable (timeout 0.2 s) -> full simulation" in r.verdict.path_reasons[-1]


def test_live_mode_without_key_is_not_configured_and_goes_to_full_simulation():
    s = Settings(vultr_inference_api_key="", llm_mode="live")
    g = Guard(s, History(), StaticState(), FakeRunner(), LLMExplainer(s), clock=lambda: 1_000_000.0)
    r = g.check(req())
    assert r.verdict.path is Path.SLOW
    assert "AI check unavailable (not configured)" in r.verdict.path_reasons[-1]


def test_assess_still_returns_none_and_never_the_mock():
    s = Settings(vultr_inference_api_key=SECRET, llm_mode="live")
    e = LLMExplainer(s, client=FakeClient(raise_429))
    assert e.assess(req(), None) is None
    with pytest.raises(LLMUnavailable) as err:
        e.assess_or_raise(req(), None)
    assert err.value.kind == "rate limited (429)" and SECRET not in str(err.value)


def test_failure_kind_never_contains_the_message():
    for exc in (openai.RateLimitError(f"x {SECRET}", response=_resp(429), body=None),
                openai.InternalServerError(f"x {SECRET}", response=_resp(503), body=None),
                ValueError(f"bad json {SECRET}"), RuntimeError(SECRET)):
        assert SECRET not in failure_kind(exc)


def test_request_options_json_mode_and_reasoning_effort():
    seen = {}

    def capture(**kw):
        seen.update(kw)
        msg = SimpleNamespace(content='{"intent_summary":"x","risk_score":5,"explanation":"y","escalate":false}')
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                               usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=100))

    s = Settings(vultr_inference_api_key=SECRET, llm_mode="live", vultr_model="glm-5.3-flash")
    e = LLMExplainer(s, client=FakeClient(capture))
    assert e.assess(req(), None).escalate is False
    assert seen["response_format"] == {"type": "json_object"}
    assert seen["extra_body"] == {"reasoning_effort": "minimal"}
    u = e.usage.snapshot()
    assert (u["calls"], u["ok"], u["prompt_tokens"], u["completion_tokens"]) == (1, 1, 1000, 100)
    assert u["cost_usd"] == pytest.approx(1000 * 0.0000001 + 100 * 0.00000035)
    assert e.model_label == "glm-5.3-flash"


def test_no_immediate_retry_after_429():
    s = Settings(vultr_inference_api_key=SECRET, llm_mode="live", llm_max_retries=1)
    e = LLMExplainer(s, client=FakeClient(raise_429))
    assert e.assess(req(), None) is None
    assert e.client.calls == 1 and e.usage.snapshot()["failures"] == {"rate limited (429)": 1}
