import json
import logging
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.models import SimulationResult, TxRequest
from llm import MOCK_TAG, LLMExplainer, describe, mock_assessment, parse_assessment
from tests.helpers import HOT, SWEEPER, tx

KEY = "vultr-SECRET-KEY-123"
GOOD = {"intent_summary": "Delegates the hot wallet to a sweeper.", "risk_score": 95,
        "explanation": "The wallet would run attacker code.", "escalate": True}
TX = TxRequest.model_validate(tx())
SIM = SimulationResult(ok=True, receipt={"status": "0x1", "logs": []},
                       state_diff={"pre": {}, "post": {HOT: {"code": "0xef0100" + SWEEPER[2:]}}})


class FakeClient:
    """Mimics openai.OpenAI().chat.completions.create with scripted replies."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=r))])


def explainer(*replies, key=KEY, mode="auto", **kw):
    s = Settings(vultr_inference_api_key=key, llm_mode=mode, **kw)
    return LLMExplainer(s, client=FakeClient(*replies))


# --- parsing ------------------------------------------------------------------

def test_parse_plain_json():
    a = parse_assessment(json.dumps(GOOD))
    assert a.risk_score == 95 and a.escalate is True and a.mock is False


def test_parse_code_fenced_json():
    assert parse_assessment("```json\n" + json.dumps(GOOD) + "\n```").risk_score == 95


def test_parse_ignores_model_claiming_mock():
    assert parse_assessment(json.dumps({**GOOD, "mock": True})).mock is False


@pytest.mark.parametrize(
    "bad",
    [
        None, "", "not json", "[1, 2]", "{broken",
        json.dumps({**GOOD, "risk_score": 101}),
        json.dumps({**GOOD, "risk_score": -1}),
        json.dumps({**GOOD, "risk_score": "50"}),
        json.dumps({**GOOD, "risk_score": 50.5}),
        json.dumps({**GOOD, "escalate": "yes"}),
        json.dumps({k: v for k, v in GOOD.items() if k != "explanation"}),
        json.dumps({**GOOD, "intent_summary": ""}),
    ],
)
def test_parse_rejects_invalid(bad):
    with pytest.raises(ValueError):
        parse_assessment(bad)


# --- live client: retry once, then None ---------------------------------------

def test_live_success_first_try():
    e = explainer(json.dumps(GOOD))
    a = e.assess(TX, SIM)
    assert a is not None and a.mock is False and a.risk_score == 95
    assert len(e.client.calls) == 1
    call = e.client.calls[0]
    assert call["model"] == "glm-5.3-flash"
    assert "0xef0100" in call["messages"][1]["content"]


def test_model_comes_from_settings():
    e = explainer(json.dumps(GOOD), vultr_model="glm-5.3")
    e.assess(TX, SIM)
    assert e.client.calls[0]["model"] == "glm-5.3"


def test_invalid_json_then_valid_retries_once():
    e = explainer("I think it is risky", json.dumps(GOOD))
    assert e.assess(TX, SIM).risk_score == 95
    assert len(e.client.calls) == 2


def test_invalid_twice_returns_none():
    e = explainer("nope", "still nope")
    assert e.assess(TX, SIM) is None
    assert len(e.client.calls) == 2


def test_timeout_twice_returns_none_never_mock():
    e = explainer(TimeoutError("read timed out"), TimeoutError("read timed out"))
    assert e.mode == "live"
    assert e.assess(TX, SIM) is None  # NOT a mock result


def test_auto_mode_with_key_never_falls_back_to_mock():
    e = explainer(RuntimeError("500"), "garbage", mode="auto")
    assert e.assess(TX, SIM) is None


def test_api_key_never_logged(caplog):
    caplog.set_level(logging.DEBUG)
    err = RuntimeError(f"401 Unauthorized: invalid key {KEY}")
    e = explainer(err, err)
    assert e.assess(TX, SIM) is None
    assert KEY not in caplog.text
    assert "LLM attempt 2/2 failed: error" in caplog.text  # the kind only, never the provider message


def test_api_key_not_in_prompt():
    e = explainer(json.dumps(GOOD))
    e.assess(TX, SIM)
    assert KEY not in json.dumps(e.client.calls)


def test_real_client_uses_vultr_base_url_and_no_sdk_retries():
    s = Settings(vultr_inference_api_key=KEY, llm_mode="live")
    c = LLMExplainer(s).client
    assert str(c.base_url).rstrip("/") == "https://api.vultrinference.com/v1"
    assert c.max_retries == 0


# --- modes / mock -------------------------------------------------------------

def test_no_key_auto_uses_marked_mock():
    e = explainer(key="")
    assert e.mode == "mock"
    a = e.assess(TX, SIM)
    assert a.mock is True
    assert a.intent_summary.startswith(MOCK_TAG) and a.explanation.startswith(MOCK_TAG)
    assert e.client.calls == []


def test_explicit_mock_mode_even_with_key():
    e = explainer(key=KEY, mode="mock")
    assert e.assess(TX, SIM).mock is True
    assert e.client.calls == []


def test_live_mode_without_key_is_off():
    e = explainer(key="", mode="live")
    assert e.mode == "off"
    assert e.assess(TX, SIM) is None


def test_mock_sees_delegation():
    a = mock_assessment(TX, SIM)
    assert "0x" + SWEEPER[2:] in a.intent_summary and a.escalate is True


def test_describe_marks_untrusted_and_lists_auths():
    t = TxRequest.model_validate(tx(authorization_list=[
        {"chain_id": 1, "address": SWEEPER, "nonce": n, "authority": HOT} for n in range(3)]))
    text = describe(t, SimulationResult(ok=False, error="timeout"))
    assert "untrusted" in text and "eip7702_authorizations: 3" in text
    assert "simulation failed: timeout" in text
