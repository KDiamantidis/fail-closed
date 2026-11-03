"""The decision engine. Deterministic rules decide; the LLM only explains.

Final verdict = the strictest action over all findings:
    any BLOCK -> BLOCK; else any NEEDS_HUMAN -> NEEDS_HUMAN; else any DELAY -> DELAY; else ALLOW.

Fail closed (R0), added here so no caller can skip it:
  - request/history rules not evaluated
  - slow path with a missing / failed / empty simulation (receipt or state diff)
  - slow path with no simulation rules configured
  - early path (sandbox skipped) without a BLOCK finding to justify it
  - a rule that crashes, misses an input, or returns something that is not a Finding list
  - an invalid request
The LLM assessment is copied into the verdict for display. It is never read when deciding,
and an LLM failure does not change the verdict.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from app.models import (
    Decision,
    Finding,
    LLMAssessment,
    Path,
    SimulationResult,
    TxRequest,
    Verdict,
    WalletType,
)


class RuleInputMissing(Exception):
    """Raised by a rule that cannot evaluate because an input it needs is absent."""


@dataclass(frozen=True)
class RuleContext:
    tx: TxRequest
    path: Path
    simulation: SimulationResult | None
    decoded: Any = None  # decode.Decoded
    settings: Any = None  # app.config.Settings
    history: Any = None  # history.History (read-only use in rules)
    state: Any = None  # state_provider.StartingState (Guard side)
    now: float = 0.0
    exclude_check: str | None = None  # re-check of a held request: do not count itself in history
    extra: dict[str, Any] = field(default_factory=dict)

    def need(self, *names: str) -> None:
        for n in names:
            if getattr(self, n) is None:
                raise RuleInputMissing(n)


Rule = Callable[[RuleContext], list[Finding]]


def r0(title: str, detail: str = "") -> Finding:
    return Finding(rule_id="R0", action=Decision.BLOCK, title=title, detail=detail)


def sandbox_problem(sim: SimulationResult | None) -> str | None:
    """Return why the simulation is unusable, or None if it is usable. Content, not exit codes."""
    if sim is None:
        return "no simulation result"
    if sim.ok is not True:
        return sim.error or "simulation failed"
    if not sim.receipt:
        return "empty receipt"
    diff = sim.state_diff
    if not diff or not isinstance(diff.get("post"), dict) or not diff["post"]:
        return "empty state diff"
    return None


def run_rules(rules: Sequence[Rule], ctx: RuleContext) -> list[Finding]:
    if not rules:
        return [r0("Rule engine error", "no rules configured")]
    findings: list[Finding] = []
    for rule in rules:
        name = getattr(rule, "__name__", repr(rule))
        try:
            out = rule(ctx)
        except RuleInputMissing as e:
            findings.append(r0("Rule engine error", f"{name}: missing input: {e}"))
            continue
        except Exception as e:  # noqa: BLE001 - any rule failure must fail closed
            findings.append(r0("Rule engine error", f"{name} crashed: {type(e).__name__}: {e}"))
            continue
        if not isinstance(out, list) or not all(isinstance(f, Finding) for f in out):
            findings.append(r0("Rule engine error", f"{name} returned an invalid result"))
            continue
        findings.extend(out)
    return findings


def combine(findings: Sequence[Finding]) -> Decision:
    return max((f.action for f in findings), key=lambda d: d.rank, default=Decision.ALLOW)


def decide(
    *,
    trace_id: str,
    tx: TxRequest | None,
    path: Path,
    simulation: SimulationResult | None,
    rules: Sequence[Rule],
    llm: LLMAssessment | None = None,
    request_findings: Sequence[Finding] | None = None,
    context: RuleContext | None = None,
    path_reasons: Sequence[str] = (),
) -> Verdict:
    """`request_findings`: results of the request/history rules (already run by the caller).
    `rules`: simulation rules, run here on the slow path (and on the fast path if given)."""
    findings: list[Finding] = []
    valid_request = tx is not None and isinstance(tx.wallet_type, WalletType)

    if not valid_request:
        findings.append(r0("Invalid request", "missing request or unknown wallet_type"))
    else:
        if request_findings is None:
            findings.append(r0("Rule engine error", "request rules were not evaluated"))
        else:
            findings.extend(request_findings)

        ctx = context or RuleContext(tx=tx, path=path, simulation=simulation)
        ctx = replace(ctx, tx=tx, path=path, simulation=simulation)

        if path is Path.SLOW:
            if (problem := sandbox_problem(simulation)) is not None:
                findings.append(r0("Sandbox failed", problem))
            else:
                findings.extend(run_rules(rules, ctx))
        elif path is Path.EARLY:
            if not any(f.action is Decision.BLOCK for f in request_findings or []):
                findings.append(r0("Rule engine error", "sandbox skipped without a BLOCK finding"))
        elif rules:
            findings.extend(run_rules(rules, ctx))

    decision = combine(findings)
    reasons = [f"[{f.rule_id}] {f.title}" + (f": {f.detail}" if f.detail else "")
               for f in findings if f.action is not Decision.ALLOW]

    return Verdict(
        verdict=decision,
        reasons=reasons,
        findings=findings,
        explanation=llm.explanation if llm is not None else None,
        intent_summary=llm.intent_summary if llm is not None else None,
        risk_score=llm.risk_score if llm is not None else None,
        llm_mock=llm.mock if llm is not None else None,
        path=path,
        path_reasons=list(path_reasons),
        wallet_type=tx.wallet_type if valid_request else None,
        chain_id=tx.chain_id if valid_request else None,
        trace_id=trace_id,
    )


def block(trace_id: str, reason: str) -> Verdict:
    """For error paths outside decide() (e.g. unexpected API exceptions)."""
    f = r0("Internal error", reason)
    return Verdict(verdict=Decision.BLOCK, reasons=[f"[R0] Internal error: {reason}"],
                   findings=[f], trace_id=trace_id)
