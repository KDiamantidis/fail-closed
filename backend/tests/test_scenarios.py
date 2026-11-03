"""Scenario files load, validate, and (where the verdict does not depend on the sandbox)
reach their expected verdicts through the real Guard with a neutral fake sandbox."""

from pathlib import Path

import pytest

from app.config import Settings
from app.models import SimulationResult
from breaker import Breaker
from decode import Kind, decode
from guard import Guard
from history import History
from llm import LLMExplainer
from scenario import discover, load
from state_provider import ScenarioStateProvider

SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"
FILES = discover(SCENARIOS)
pytestmark = pytest.mark.skipif(not FILES, reason="scenarios/ not available (backend copied alone)")
VERDICTS = {"ALLOW", "DELAY", "NEEDS_HUMAN", "BLOCK"}
PATHS = {"fast", "slow", "early"}


class NeutralRunner:
    """A sandbox that sees nothing special: the verdict comes from request/history rules."""

    def run(self, tx, state):
        return SimulationResult(ok=True, receipt={"status": "0x1", "logs": []},
                                state_diff={"pre": {"0x" + "ee" * 20: {"balance": "0x0"}},
                                            "post": {"0x" + "ee" * 20: {"nonce": 1}}})


def test_expected_scenarios_exist_in_order():
    assert [p.stem[:2] for p in FILES] == ["a_", "b_", "c_", "d_", "e_"]


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_scenario_file_is_valid(path):
    sc = load(path)
    settings = sc.settings(Settings(llm_mode="mock"))
    assert sc.requests
    for r in sc.requests:
        tx = r.tx()
        assert r.expect.verdict in VERDICTS
        assert r.expect.path in PATHS | {None}
        assert decode(tx, settings.known_tokens).kind is not None
    # the request never carries state; the state section is Guard-side
    assert all("state" not in r.request and "balance" not in r.request for r in sc.requests)


def test_mercado_tokens_are_known_transfers():
    sc = load(SCENARIOS / "e_mercado_multichain.json")
    s = sc.settings(Settings())
    kinds = [decode(r.tx(), s.known_tokens).kind for r in sc.requests]
    assert kinds.count(Kind.ERC20_TRANSFER) == 18
    assert {r.tx().chain_id for r in sc.requests} == {1, 10, 42161, 137, 43114}


@pytest.mark.parametrize("name", ["a_customer_small_withdrawal", "b_treasury_allowlisted_above_limit",
                                  "c_treasury_unknown_above_limit", "e_mercado_multichain"])
def test_scenario_reaches_expected_verdicts_with_neutral_sandbox(name):
    sc = load(SCENARIOS / f"{name}.json")
    settings = sc.settings(Settings(llm_mode="mock"))
    history = History()
    clock = {"now": 1_000_000.0}
    g = Guard(settings, history, ScenarioStateProvider(sc.path), NeutralRunner(), LLMExplainer(settings),
              breaker=Breaker(history), clock=lambda: clock["now"])
    for r in sc.requests:
        clock["now"] = 1_000_000.0 + r.t
        v = g.check(r.tx()).verdict
        assert v.verdict.value == r.expect.verdict, (r.label, v.reasons)
        if r.expect.path:
            assert v.path.value == r.expect.path, (r.label, v.path_reasons)
        assert set(r.expect.rules) <= {f.rule_id for f in v.findings}, (r.label, v.reasons)
