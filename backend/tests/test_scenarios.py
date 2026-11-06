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


def neutral_guard(name):
    sc = load(SCENARIOS / f"{name}.json")
    settings = sc.settings(Settings(llm_mode="mock"))
    history = History()
    g = Guard(settings, history, ScenarioStateProvider(sc.path), NeutralRunner(), LLMExplainer(settings),
              breaker=Breaker(history))
    return sc, g


def test_expected_scenarios_exist_in_order():
    assert [p.stem[:2] for p in FILES] == ["a_", "b_", "c_", "d_", "e_", "f_", "g_"]


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_scenario_file_is_valid(path):
    sc = load(path)
    settings = sc.settings(Settings(llm_mode="mock"))
    assert sc.requests
    for r in sc.requests:
        if r.is_action:
            assert r.expect.verdict is None
            continue
        tx = r.tx()
        assert r.expect.verdict in VERDICTS
        assert r.expect.path in PATHS | {None}
        assert decode(tx, settings.known_tokens).kind is not None
    # the request never carries state; the state section is Guard-side
    assert all("state" not in r.request and "balance" not in r.request for r in sc.requests if not r.is_action)


def test_multichain_drain_tokens_are_known_transfers():
    sc = load(SCENARIOS / "e_multichain_drain.json")
    s = sc.settings(Settings())
    kinds = [decode(r.tx(), s.known_tokens).kind for r in sc.requests]
    assert kinds.count(Kind.ERC20_TRANSFER) == 18
    assert {r.tx().chain_id for r in sc.requests} == {1, 10, 42161, 137, 43114}


@pytest.mark.parametrize("name", ["a_customer_small_withdrawal", "b_treasury_allowlisted_above_limit",
                                  "c_treasury_unknown_above_limit", "e_multichain_drain",
                                  "f_honest_large_withdrawal"])
def test_scenario_reaches_expected_verdicts_with_neutral_sandbox(name):
    sc, g = neutral_guard(name)
    for r in sc.requests:
        g.clock = lambda r=r: 1_000_000.0 + r.t
        v = g.check(r.tx()).verdict
        if r.expect.wallet_paused is not None:
            assert (g.history.is_paused(r.tx().from_) is not None) == r.expect.wallet_paused, r.label
        assert v.verdict.value == r.expect.verdict, (r.label, v.reasons)
        if r.expect.path:
            assert v.path.value == r.expect.path, (r.label, v.path_reasons)
        assert set(r.expect.rules) <= {f.rule_id for f in v.findings}, (r.label, v.reasons)


def test_multichain_drain_blast_radius_counts_only_allowed_before_the_freeze():
    from cli import blast_radius

    sc, g = neutral_guard("e_multichain_drain")
    for r in sc.requests:
        g.clock = lambda r=r: 1_000_000.0 + r.t
        g.check(r.tx())
    text = "\n".join(blast_radius(g.history, sc))
    assert "frozen by R7" in text
    assert "chain      1: 40.0001 ETH, 50000 MOCK01" in text
    assert "chain     10: 50000 MOCK06" in text
    assert "total: 40.0001 ETH + 300,000 tokens (6 token types)" in text  # MOCK01-06 allowed
    assert "chain  42161: 50000 MOCK07" in text and "chain    137: 50000 MOCK08" in text  # held
    assert "MOCK10" not in text  # blocked after the freeze: never counted
