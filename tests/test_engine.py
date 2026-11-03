"""The fail-closed truth table. ALLOW only when every input is positively good."""

import itertools

import pytest

from app.models import Decision, Finding, LLMAssessment, Severity, SimulationResult
from policies.engine import block, decide

SIM_OK = SimulationResult(ok=True)
SIM_FAIL = SimulationResult(ok=False, error="timeout after 30s")
BLOCKING = Finding(rule_id="approval.unlimited", severity=Severity.CRITICAL, blocking=True, title="Unlimited")
INFO = Finding(rule_id="info.note", severity=Severity.INFO, blocking=False, title="Note")
LLM_ALLOW = LLMAssessment(block=False, risk=Severity.LOW, explanation="Plain transfer of 1 ETH to Bob.")
LLM_BLOCK = LLMAssessment(block=True, risk=Severity.HIGH, explanation="Looks like a drainer.")


def verdict(sim=SIM_OK, findings=(), llm=LLM_ALLOW, llm_error=None):
    return decide(trace_id="t", simulation=sim, findings=list(findings), llm=llm, llm_error=llm_error)


def test_all_good_allows():
    v = verdict(findings=[INFO])
    assert v.verdict == Decision.ALLOW
    assert v.reasons == []
    assert v.explanation == LLM_ALLOW.explanation


def test_llm_allow_cannot_override_deterministic_block():
    v = verdict(findings=[BLOCKING], llm=LLM_ALLOW)
    assert v.verdict == Decision.BLOCK
    assert any("approval.unlimited" in r for r in v.reasons)
    assert v.findings[0].blocking is True


def test_llm_can_add_block():
    v = verdict(llm=LLM_BLOCK)
    assert v.verdict == Decision.BLOCK
    assert v.explanation == LLM_BLOCK.explanation


@pytest.mark.parametrize("sim", [SIM_FAIL, None])
def test_simulation_failure_blocks(sim):
    v = verdict(sim=sim)
    assert v.verdict == Decision.BLOCK
    assert "Simulation did not complete" in v.reasons[0]


def test_llm_error_blocks():
    v = verdict(llm=None, llm_error="timeout after retry")
    assert v.verdict == Decision.BLOCK
    assert "Agent unavailable" in v.reasons[0]
    assert v.explanation.startswith("Blocked without an agent explanation")


def test_llm_missing_blocks():
    assert verdict(llm=None).verdict == Decision.BLOCK


def test_llm_error_wins_even_if_assessment_present():
    assert verdict(llm=LLM_ALLOW, llm_error="malformed tool call").verdict == Decision.BLOCK


@pytest.mark.parametrize(
    "sim,findings,llm,llm_error",
    list(
        itertools.product(
            [SIM_OK, SIM_FAIL, None],
            [(), (INFO,), (BLOCKING,), (INFO, BLOCKING)],
            [LLM_ALLOW, LLM_BLOCK, None],
            [None, "err"],
        )
    ),
)
def test_truth_table(sim, findings, llm, llm_error):
    v = verdict(sim, findings, llm, llm_error)
    expect_allow = sim is SIM_OK and BLOCKING not in findings and llm is LLM_ALLOW and llm_error is None
    assert v.verdict == (Decision.ALLOW if expect_allow else Decision.BLOCK)
    # Every BLOCK says why; every ALLOW has no reasons.
    assert bool(v.reasons) == (v.verdict == Decision.BLOCK)


def test_block_helper():
    v = block("t", "invalid request")
    assert v.verdict == Decision.BLOCK
    assert v.reasons == ["invalid request"]
