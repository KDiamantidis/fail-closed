"""The fail-closed combiner.

ALLOW requires every one of these to be positively true:
  - the simulation ran and reported ok
  - no deterministic finding is blocking
  - the LLM step succeeded and returned block=False
Anything else (including missing inputs) is BLOCK. The LLM output is only ever
an extra reason to block; it is not consulted when deciding to un-block.
"""

from app.models import Decision, Finding, LLMAssessment, SimulationResult, Verdict


def decide(
    *,
    trace_id: str,
    simulation: SimulationResult | None,
    findings: list[Finding],
    llm: LLMAssessment | None,
    llm_error: str | None = None,
) -> Verdict:
    reasons: list[str] = []

    sim_ok = simulation is not None and simulation.ok is True
    if not sim_ok:
        err = simulation.error if simulation is not None else "not run"
        reasons.append(f"Simulation did not complete: {err or 'unknown error'}")

    blocking = [f for f in findings if f.blocking]
    for f in blocking:
        reasons.append(f"[{f.rule_id}] {f.title}")

    llm_ok = llm is not None and llm_error is None
    if llm_error is not None:
        reasons.append(f"Agent unavailable: {llm_error}")
    elif llm is None:
        reasons.append("Agent returned no assessment")
    elif llm.block is not False:
        reasons.append(f"Agent flagged {llm.risk.value} risk")

    allow = sim_ok and not blocking and llm_ok and llm is not None and llm.block is False

    if allow and reasons:  # cannot happen; defensive
        allow = False
    if not allow and not reasons:  # cannot happen; never BLOCK without saying why
        reasons.append("Result unclear")

    if llm_ok and llm is not None:
        explanation = llm.explanation
    elif reasons:
        explanation = "Blocked without an agent explanation. " + "; ".join(reasons)
    else:
        explanation = ""

    return Verdict(
        verdict=Decision.ALLOW if allow else Decision.BLOCK,
        reasons=reasons,
        findings=list(findings),
        explanation=explanation,
        trace_id=trace_id,
        simulation=simulation.model_dump() if simulation is not None else None,
    )


def block(trace_id: str, reason: str) -> Verdict:
    """Shortcut for error paths: BLOCK with a single reason."""
    return Verdict(
        verdict=Decision.BLOCK,
        reasons=[reason],
        findings=[],
        explanation=f"Blocked: {reason}",
        trace_id=trace_id,
    )
