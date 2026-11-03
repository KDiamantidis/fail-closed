"""Decision engine: rules decide, strictest wins, R0 fails closed, the LLM never matters."""

import itertools

import pytest

from app.models import Decision, Finding, LLMAssessment, Path, SimulationResult, TxRequest
from policies.engine import RuleInputMissing, block, combine, decide
from tests.helpers import tx

TX = TxRequest.model_validate(tx())
SIM_OK = SimulationResult(
    ok=True,
    receipt={"status": "0x1"},
    state_diff={"pre": {}, "post": {"0xabc": {"balance": "0x1"}}},
)

LLM_CASES = [
    None,  # LLM failed / not called
    LLMAssessment(intent_summary="Plain transfer.", risk_score=0, explanation="Looks fine.", escalate=False),
    LLMAssessment(intent_summary="Drainer!", risk_score=100, explanation="Looks like a drainer!", escalate=True),
]


def f(action: Decision, rule_id: str = "RX") -> Finding:
    return Finding(rule_id=rule_id, action=action, title=f"{rule_id} says {action}")


def emits(*findings: Finding):
    def rule(ctx):
        return list(findings)
    return rule


def run(rules, sim=SIM_OK, path=Path.SLOW, llm=None, request=TX):
    return decide(trace_id="t", tx=request, path=path, simulation=sim, rules=rules, llm=llm)


# --- strictest wins -------------------------------------------------------------

ACTION_SETS = [
    combo
    for n in range(0, 4)
    for combo in itertools.combinations_with_replacement(list(Decision), n)
]


@pytest.mark.parametrize("actions", ACTION_SETS)
def test_truth_table_strictest_wins(actions):
    expected = Decision.ALLOW
    for d in (Decision.DELAY, Decision.NEEDS_HUMAN, Decision.BLOCK):
        if d in actions:
            expected = d
    v = run([emits(*[f(a, f"R{i}") for i, a in enumerate(actions)])])
    assert v.verdict == expected
    # Every non-ALLOW verdict has at least one reason; ALLOW has none.
    assert bool(v.reasons) == (v.verdict is not Decision.ALLOW)
    assert len(v.reasons) == sum(a is not Decision.ALLOW for a in actions)


def test_combine_empty_is_allow():
    assert combine([]) == Decision.ALLOW


def test_findings_from_multiple_rules_are_merged():
    v = run([emits(f(Decision.DELAY, "R7")), emits(f(Decision.NEEDS_HUMAN, "R5"))])
    assert v.verdict == Decision.NEEDS_HUMAN
    assert [x.rule_id for x in v.findings] == ["R7", "R5"]


# --- the LLM never changes the verdict -------------------------------------------

@pytest.mark.parametrize("actions", ACTION_SETS)
@pytest.mark.parametrize("sim", [SIM_OK, None, SimulationResult(ok=False, error="timeout")])
def test_llm_never_changes_verdict(actions, sim):
    rules = [emits(*[f(a) for a in actions])]
    verdicts = {run(rules, sim=sim, llm=llm).verdict for llm in LLM_CASES}
    assert len(verdicts) == 1


def test_llm_risk_score_cannot_lower_block():
    v = run([emits(f(Decision.BLOCK, "R1"))], llm=LLM_CASES[1])  # score 0, "looks fine"
    assert v.verdict == Decision.BLOCK
    assert v.risk_score == 0
    assert v.explanation == "Looks fine."


def test_llm_risk_score_cannot_raise_allow():
    v = run([emits()], llm=LLM_CASES[2])  # score 100
    assert v.verdict == Decision.ALLOW
    assert v.risk_score == 100


def test_llm_failure_means_no_explanation_same_verdict():
    v = run([emits(f(Decision.DELAY, "R7"))], llm=None)
    assert v.verdict == Decision.DELAY
    assert v.explanation is None and v.risk_score is None


# --- R0: sandbox fails closed (slow path) ----------------------------------------

@pytest.mark.parametrize(
    "sim,why",
    [
        (None, "no simulation result"),
        (SimulationResult(ok=False, error="timeout after 30s"), "timeout after 30s"),
        (SimulationResult(ok=False), "simulation failed"),
        # "ok" but empty content must still block: never trust the flag alone.
        (SimulationResult(ok=True, receipt=None, state_diff=SIM_OK.state_diff), "empty receipt"),
        (SimulationResult(ok=True, receipt={}, state_diff=SIM_OK.state_diff), "empty receipt"),
        (SimulationResult(ok=True, receipt={"status": "0x1"}, state_diff=None), "empty state diff"),
        (SimulationResult(ok=True, receipt={"status": "0x1"}, state_diff={"pre": {}, "post": {}}), "empty state diff"),
        (SimulationResult(ok=True, receipt={"status": "0x1"}, state_diff={"pre": {}}), "empty state diff"),
    ],
)
def test_r0_bad_simulation_blocks(sim, why):
    v = run([emits()], sim=sim)
    assert v.verdict == Decision.BLOCK
    assert v.findings[0].rule_id == "R0"
    assert why in v.reasons[0]


def test_r0_bad_simulation_overrides_allowing_rules():
    v = run([emits(f(Decision.ALLOW))], sim=None)
    assert v.verdict == Decision.BLOCK


def test_fast_path_needs_no_simulation():
    v = run([emits()], sim=None, path=Path.FAST)
    assert v.verdict == Decision.ALLOW


# --- R0: rule engine errors -----------------------------------------------------

def crashing_rule(ctx):
    raise ZeroDivisionError("boom")


def missing_input_rule(ctx):
    raise RuleInputMissing("token balance")


def bad_return_rule(ctx):
    return "ALLOW"


@pytest.mark.parametrize(
    "rule,why",
    [
        (crashing_rule, "crashing_rule crashed: ZeroDivisionError"),
        (missing_input_rule, "missing_input_rule: missing input: token balance"),
        (bad_return_rule, "bad_return_rule returned an invalid result"),
    ],
)
def test_r0_rule_engine_error_blocks(rule, why):
    v = run([emits(), rule, emits(f(Decision.DELAY))])
    assert v.verdict == Decision.BLOCK
    r0 = [x for x in v.findings if x.rule_id == "R0"]
    assert len(r0) == 1 and why in r0[0].detail
    # Other rules still ran and are reported.
    assert any(x.action == Decision.DELAY for x in v.findings)


def test_r0_no_rules_blocks():
    v = run([])
    assert v.verdict == Decision.BLOCK
    assert "no rules configured" in v.reasons[0]


# --- R0: invalid request reaching decide() --------------------------------------

def test_missing_request_blocks():
    assert run([emits()], request=None).verdict == Decision.BLOCK


def test_unknown_wallet_type_blocks():
    bad = TxRequest.model_construct(**{**TX.model_dump(), "wallet_type": "cold"})
    v = run([emits()], request=bad)
    assert v.verdict == Decision.BLOCK
    assert "wallet_type" in v.reasons[0]


def test_verdict_carries_request_context():
    v = run([emits()])
    assert (v.wallet_type, v.chain_id, v.path) == (TX.wallet_type, 1, Path.SLOW)


def test_block_helper():
    v = block("t", "unexpected error")
    assert v.verdict == Decision.BLOCK
    assert v.reasons == ["[R0] Internal error: unexpected error"]
