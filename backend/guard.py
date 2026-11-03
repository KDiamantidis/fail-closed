"""The Guard: one check() per signing request.

decode -> request/history rules -> route (fast / slow / early) -> decide() -> history
-> breaker effects.

Routing:
- early: the destination is frozen (F1) or the wallet is paused (F2): a latched breaker
  stop, so the sandbox is skipped; decide() checks that a BLOCK finding really exists.
  Every other BLOCK still runs the sandbox, so the verdict comes with evidence.
- fast: ALL of these hold, else slow:
    customer_hot: native_transfer, or erc20_transfer to a known token; no authorization_list;
                  no request/history finding at all.
    treasury:     the same shape, destination on the allowlist, amount under the limit,
                  no finding.
    FAST_PATH_LLM_ESCALATION on: the LLM is asked; escalate=true, an error or a timeout
                  -> slow path (more checking, never less). The LLM never sets a verdict.
- slow: fresh sandbox, state diff, probe, simulation rules.
Every check records why it took its path. Any unexpected error -> BLOCK (and recorded).
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.config import Settings
from app.models import Decision, LLMAssessment, Path, SimulationResult, TxRequest, Verdict, WalletType
from decode import Decoded, Kind, authority_of, decode
from history import CheckRecord, History
from policies.engine import RuleContext, block, decide, run_rules
from policies.request_rules import REQUEST_RULES
from policies.sim_rules import SIM_RULES
from sandbox_runner import SandboxError
from state_provider import StartingState, StateProvider

log = logging.getLogger(__name__)
FAST_KINDS = (Kind.NATIVE_TRANSFER, Kind.ERC20_TRANSFER)
LATCHED = {"F1", "F2"}  # frozen destination / paused wallet: nothing a simulation could change


@dataclass
class CheckResult:
    check_id: str
    verdict: Verdict
    decoded: Decoded | None
    simulation: SimulationResult | None = None
    llm: LLMAssessment | None = None
    timings: dict[str, float] = field(default_factory=dict)
    breaker_actions: list[str] = field(default_factory=list)


class Guard:
    def __init__(self, settings: Settings, history: History, state_provider: StateProvider,
                 runner: Any, explainer: Any, breaker: Any = None,
                 clock=time.time, perf=time.perf_counter):
        self.settings = settings
        self.history = history
        self.state_provider = state_provider
        self.runner = runner
        self.explainer = explainer
        self.breaker = breaker
        self.clock = clock  # wall clock for history windows (injected in tests)
        self.perf = perf  # monotonic clock for step timings

    def check(self, tx: TxRequest) -> CheckResult:
        check_id = uuid.uuid4().hex
        now = self.clock()
        t_start = self.perf()
        timings: dict[str, float] = {}
        result = CheckResult(check_id=check_id, verdict=block(check_id, "not evaluated"), decoded=None)
        try:
            self._check(tx, check_id, now, timings, result)
        except Exception as e:  # noqa: BLE001 - fail closed on anything unexpected
            log.exception("guard check failed")
            result.verdict = block(check_id, f"guard error: {type(e).__name__}: {e}")
        timings["total"] = self.perf() - t_start
        result.timings = timings
        self._record(tx, result, now)
        if self.breaker is not None:
            try:
                result.breaker_actions = self.breaker.apply(tx, result.verdict, now, check_id)
            except Exception:  # noqa: BLE001
                log.exception("breaker failed")
                result.breaker_actions = ["breaker error (see log)"]
        return result

    # ---- internals ------------------------------------------------------------

    def _timed(self, timings: dict, key: str, fn, *args):
        t0 = self.perf()
        try:
            return fn(*args)
        finally:
            timings[key] = timings.get(key, 0.0) + self.perf() - t0

    def _check(self, tx: TxRequest, check_id: str, now: float, timings: dict, result: CheckResult) -> None:
        s = self.settings
        decoded = decode(tx, s.known_tokens)
        result.decoded = decoded

        state: StartingState | None
        try:
            state = self._timed(timings, "state", self.state_provider.get_state, tx)
        except Exception as e:  # noqa: BLE001 - rules needing state will fail closed (R0)
            log.warning("state provider failed: %s", e)
            state = None

        ctx = RuleContext(tx=tx, path=Path.FAST, simulation=None, decoded=decoded, settings=s,
                          history=self.history, state=state, now=now)
        req_findings = self._timed(timings, "request_rules", run_rules, REQUEST_RULES, ctx)

        path, reasons, llm = self._route(tx, decoded, req_findings, timings)

        sim: SimulationResult | None = None
        if path is Path.SLOW:
            if state is None:
                sim = SimulationResult(ok=False, error="no starting state from the StateProvider")
            else:
                try:
                    sim = self._timed(timings, "sandbox", self.runner.run, tx, state)
                except SandboxError as e:
                    sim = SimulationResult(ok=False, error=str(e))
            for k, v in (sim.timings or {}).items():
                timings[f"sandbox.{k}"] = v
            llm = self._timed(timings, "llm", self._explain, tx, sim)
        elif path is Path.EARLY:
            llm = self._timed(timings, "llm", self._explain, tx, None)

        verdict = self._timed(timings, "decide", lambda: decide(
            trace_id=check_id, tx=tx, path=path, simulation=sim,
            rules=SIM_RULES if path is Path.SLOW else [], llm=llm,
            request_findings=req_findings, context=ctx, path_reasons=reasons))
        if verdict.verdict is Decision.DELAY:
            verdict.release_at = now + s.delay_minutes * 60
        result.verdict, result.simulation, result.llm = verdict, sim, llm

    def _route(self, tx: TxRequest, d: Decoded, findings, timings) -> tuple[Path, list[str], LLMAssessment | None]:
        s = self.settings
        ids = sorted({f.rule_id for f in findings})
        latched = sorted({f.rule_id for f in findings if f.rule_id in LATCHED and f.action is Decision.BLOCK})
        if latched:
            return Path.EARLY, [f"latched breaker ({', '.join(latched)}): destination frozen or wallet paused; "
                                "sandbox not needed"], None

        why_not: list[str] = []
        if d.kind not in FAST_KINDS:
            why_not.append(f"shape is {d.kind.value}, not a plain transfer")
        if tx.authorization_list is not None:
            why_not.append("request carries an authorization_list")
        if findings:
            why_not.append(f"request/history rules found {', '.join(ids)}")
        if tx.wallet_type is WalletType.TREASURY:
            if d.destination not in s.treasury_allowlist:
                why_not.append("treasury destination not on the allowlist")
            if tx.value > s.r4_treasury_limit_wei or d.kind is Kind.ERC20_TRANSFER:
                why_not.append("treasury amount not under the limit")
        if why_not:
            return Path.SLOW, why_not, None

        shape = f"{d.kind.value}" + (" to a known token" if d.kind is Kind.ERC20_TRANSFER else "")
        reasons = [f"known-safe shape ({shape}), no request/history findings"]
        if not s.fast_path_llm_escalation:
            return Path.FAST, reasons + ["LLM escalation off"], None
        llm = self._timed(timings, "llm", self._explain, tx, None)
        if llm is None:
            return Path.SLOW, reasons + ["LLM error or timeout -> slow path (more checking, never less)"], None
        if llm.escalate:
            return Path.SLOW, reasons + ["LLM asked for a deeper check"], llm
        return Path.FAST, reasons + ["LLM: no escalation" + (" (MOCK)" if llm.mock else "")], llm

    def _explain(self, tx: TxRequest, sim: SimulationResult | None) -> LLMAssessment | None:
        try:
            return self.explainer.assess(tx, sim)
        except Exception as e:  # noqa: BLE001 - LLM failure never changes the verdict
            log.warning("explainer raised: %s", e)
            return None

    def _record(self, tx: TxRequest, r: CheckResult, now: float) -> None:
        d, v = r.decoded, r.verdict
        try:
            self.history.record_check(CheckRecord(
                id=r.check_id, ts=now, wallet=tx.from_, chain_id=tx.chain_id,
                wallet_type=tx.wallet_type.value, kind=d.kind.value if d else "unknown",
                destination=d.destination if d else tx.to, asset=d.asset if d else "native",
                amount=d.amount if d else tx.value,
                path=v.path.value if v.path else "none", path_reasons=v.path_reasons,
                verdict=v.verdict.value, findings=[f.model_dump(mode="json") for f in v.findings],
                timings=r.timings, llm=r.llm.model_dump() if r.llm else None,
                request=tx.model_dump(mode="json", by_alias=True), release_at=v.release_at,
                auth_nonces=[(authority_of(a, tx), a.chain_id, a.nonce) for a in tx.authorization_list or []],
            ))
        except Exception:  # noqa: BLE001
            log.exception("could not record check %s", r.check_id)
