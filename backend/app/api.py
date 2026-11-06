"""Fail Closed HTTP API. Runs on the host (uvicorn), never in a container.

    POST /check                 dry run: verdict + findings + path + timings + LLM, NEVER an approval
    POST /withdraw              demo flow: Guard check -> approval -> mock signer
    GET  /history?limit=        newest first
    GET  /breaker               frozen destinations + paused wallets
    POST /breaker/reset         {address} or {wallet}; header X-Admin-Token
    GET  /stream                Server-Sent Events, one per check step
    GET  /events?after=         the same events as JSON (catch-up)
    GET  /scenarios             demo scenarios
    POST /scenarios/{name}/run  run a scenario's requests through /withdraw
    GET  /health, GET /guard/public-key

No response ever contains a secret: no settings, no API keys, no private keys.

    uvicorn app.api:app --host 127.0.0.1 --port 8000      (from backend/)
"""

import asyncio
from collections import deque
import json
import re
from collections import OrderedDict
import logging
import secrets
import threading
import time
import urllib.request
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from app.config import REPO_ROOT, Settings, get_settings
from app.models import TxRequest
from approval import Approval, Approver, GuardKey, request_hash
from breaker import Breaker, level_of
from events import EventBus
from guard import CheckResult, Guard
from history import History
from reports import blast_radius_data
from loadtest import run_config
from redteam import RedTeam
from holds import HoldError, HoldManager
from llm import LLMExplainer
from sandbox_gate import GuardBusy, SandboxGate
from sandbox_runner import SandboxRunner
from scenario import discover, load, merged_policy, merged_state
from signer_mock import MockSigner, ReplayStore
from state_provider import StaticStateProvider

log = logging.getLogger(__name__)


class DecisionRequest(BaseModel):
    actor: str = "api-admin"  # who decides; written to the log


class DemoReset(BaseModel):
    run_ids: list[str] = Field(default_factory=list, max_length=50)


class ResetRequest(BaseModel):
    address: str | None = None  # level 1: unfreeze a destination
    wallet: str | None = None  # level 2: unpause a wallet
    chain_id: int | None = None  # level 3: end safe mode on a chain
    global_stop: bool | None = None  # level 4: lift the global stop
    actor: str = "api-admin"

    @model_validator(mode="after")
    def _one(self) -> "ResetRequest":
        given = [self.address is not None, self.wallet is not None, self.chain_id is not None,
                 bool(self.global_stop)]
        if sum(given) != 1:
            raise ValueError("give exactly one of address, wallet, chain_id or global_stop")
        return self


DEMO_RUN_TTL_SECONDS = 30 * 60
DEMO_RUN_MAX = 50
TRUSTED_PROXIES = ("127.0.0.1", "::1")  # Caddy / the tunnel on the same host


class RateLimiter:
    """Per-IP sliding windows (public demo). In memory: one API process (one uvicorn worker)."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._hits: dict[tuple[str, str], deque] = {}
        self._lock = threading.Lock()

    def hit(self, what: str, ip: str, limit: int, window: float, words: str) -> None:
        """Count one request; over the limit -> 429 with a plain message (and Retry-After)."""
        if limit <= 0:
            return
        now = self.clock()
        with self._lock:
            q = self._hits.setdefault((what, ip), deque())
            while q and q[0] <= now - window:
                q.popleft()
            if len(q) >= limit:
                wait = max(1, int(q[0] + window - now) + 1)
                raise HTTPException(status_code=429, headers={"Retry-After": str(wait)},
                                    detail=f"Too many {words} from your address: at most {limit} per "
                                           f"{_window_words(window)}. Please try again in {wait} s.")
            q.append(now)
            if len(self._hits) > 10_000:  # bounded memory: forget idle addresses
                for key in [k for k, v in self._hits.items() if not v or v[-1] <= now - 3600]:
                    del self._hits[key]


def _window_words(seconds: float) -> str:
    return "minute" if seconds == 60 else f"{seconds / 60:g} minutes"


def client_ip(request: Request) -> str:
    """The visitor's address. Behind our own proxy (Caddy or the tunnel on this host) it is the
    last X-Forwarded-For entry, the one the proxy added; otherwise the socket peer."""
    peer = request.client.host if request.client else "unknown"
    fwd = request.headers.get("x-forwarded-for", "")
    if peer in TRUSTED_PROXIES and fwd:
        return fwd.split(",")[-1].strip() or peer
    return peer
RUN_ID = re.compile(r"^[a-z0-9]{8,32}$")
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"


def http_health(url: str) -> dict[str, Any]:
    """The red-team health probe: a real HTTP call to our own /health."""
    with urllib.request.urlopen(url, timeout=2) as r:  # noqa: S310 - our own fixed URL
        return {"ok": r.status == 200}


def check_payload(res: CheckResult) -> dict[str, Any]:
    v, d, sim = res.verdict, res.decoded, res.simulation
    return {
        "check_id": res.check_id,
        "verdict": v.verdict.value,
        "path": v.path.value if v.path else None,
        "path_reasons": v.path_reasons,
        "reasons": v.reasons,
        "findings": [f.model_dump(mode="json") for f in v.findings],
        "release_at": v.release_at,
        "decoded": {"kind": d.kind.value, "destination": d.destination, "asset": d.asset,
                    "amount": str(d.amount), "note": d.note} if d else None,
        "llm": res.llm.model_dump() if res.llm else None,
        "simulation": {
            "ok": sim.ok, "error": sim.error, "delegation_mode": sim.delegation_mode,
            "receipt_status": (sim.receipt or {}).get("status"),
            "state_diff": sim.state_diff, "probe": sim.probe, "sandbox_limits": sim.sandbox,
        } if sim else None,
        "breaker": res.breaker_actions,
        "explanation_pending": res.explanation is not None and not res.explanation.done(),
        "timings_ms": {k: round(t * 1000, 1) for k, t in res.timings.items()},
    }


class Services:
    """Everything the API needs; built once, injectable for tests."""

    def __init__(self, settings: Settings, guard: Guard, history: History, approver: Approver,
                 signer: MockSigner, bus: EventBus, scenarios_dir: Path, run_id: str = "main"):
        self.settings, self.guard, self.history = settings, guard, history
        self.approver, self.signer, self.bus, self.scenarios_dir = approver, signer, bus, scenarios_dir
        self.run_id = run_id
        self.breaker = Breaker(history, settings)
        self.holds = HoldManager(guard, approver, signer, publish=self.publish)
        self.redteam_lock = threading.Lock()  # one red-team run at a time
        self.loadtest_lock = threading.Lock()  # one load demo at a time
        self.loadtest_runner: Any = None  # override in tests; default: a real SandboxRunner
        self.health_probe: Any = None  # override in tests; default: HTTP GET of our own /health
        # Demo runs stay alive for a while so a visitor can approve/reject NEEDS_HUMAN items in
        # THEIR OWN isolated run (never the main state). Bounded: oldest runs are dropped.
        self.demo_runs: "OrderedDict[str, tuple[Services, float]]" = OrderedDict()
        self.demo_lock = threading.Lock()

    def demo_ttl(self) -> float:
        """Public demo: a visitor's run is dropped after N idle minutes (auto-reset)."""
        if self.settings.public_demo:
            return self.settings.demo_idle_reset_minutes * 60
        return DEMO_RUN_TTL_SECONDS

    def remember_demo(self, run_id: str, iso: "Services") -> None:
        with self.demo_lock:
            now = time.time()
            for rid in [r for r, (_, exp) in self.demo_runs.items() if exp < now]:
                del self.demo_runs[rid]
            self.demo_runs[run_id] = (iso, now + self.demo_ttl())
            while len(self.demo_runs) > DEMO_RUN_MAX:
                self.demo_runs.popitem(last=False)

    def demo(self, run_id: str) -> "Services":
        with self.demo_lock:
            entry = self.demo_runs.get(run_id)
            now = time.time()
            if entry is None or entry[1] < now:
                self.demo_runs.pop(run_id, None)
                raise HTTPException(status_code=404, detail=(
                    "This demo was reset (no activity for a while, or Reset demo). Run the replay again."))
            self.demo_runs[run_id] = (entry[0], now + self.demo_ttl())  # activity keeps it alive
        return entry[0]

    def forget_demos(self, run_ids: list[str]) -> int:
        """Reset demo: drop these runs with their breaker, queue and history (level 0 again)."""
        with self.demo_lock:
            return sum(1 for rid in run_ids if self.demo_runs.pop(rid, None) is not None)

    def make_redteam(self, probe) -> RedTeam:
        return RedTeam(self.settings, health_probe=probe, gate=self.guard.gate)

    def publish(self, check_id: str, kind: str, data: dict) -> None:
        self.bus.publish(check_id, kind, data, run_id=self.run_id)

    def isolated(self, run_id: str) -> "Services":
        """A demo run with its OWN fresh state: in-memory history, breaker and replay store.
        Same Guard parts (policy, Guard-side state, sandbox, LLM, approval key). Nothing it
        does is visible to the main state or to another visitor's run."""
        history = History(":memory:")
        g = self.guard
        guard = Guard(g.settings, history, g.state_provider, g.runner, g.explainer,
                      breaker=Breaker(history, g.settings), clock=g.clock, perf=g.perf,
                      events=lambda cid, kind, data: self.bus.publish(cid, kind, data, run_id=run_id),
                      gate=g.gate)
        signer = MockSigner(self.signer.guard_public_key_hex, ReplayStore(":memory:"), clock=self.signer.clock)
        return Services(self.settings, guard, history, self.approver, signer, self.bus, self.scenarios_dir,
                        run_id=run_id)

    @classmethod
    def default(cls, base: Settings | None = None, scenarios_dir: Path = REPO_ROOT / "scenarios") -> "Services":
        base = base or get_settings()
        scenarios = [load(p) for p in discover(scenarios_dir)]
        settings = Settings(**{**base.model_dump(), **merged_policy(scenarios)})
        history = History(settings.history_db)
        bus = EventBus()
        guard = Guard(settings, history, StaticStateProvider(merged_state(scenarios)),
                      SandboxRunner(settings), LLMExplainer(settings), breaker=Breaker(history, settings),
                      events=lambda cid, kind, data: bus.publish(cid, kind, data, run_id="main"),
                      gate=SandboxGate(settings.sandbox_max_concurrent, settings.sandbox_queue_seconds))
        key = GuardKey.load_or_create(settings.guard_key_path)
        approver = Approver(key, settings.approval_ttl_seconds)
        signer = MockSigner(key.public_key_hex, ReplayStore(settings.signer_db))  # public key only
        return cls(settings, guard, history, approver, signer, bus, scenarios_dir)

    def withdraw(self, tx: TxRequest) -> dict[str, Any]:
        res = self.guard.check(tx)  # GuardBusy propagates: the API answers 503, nothing is signed
        approval = self.approver.issue(tx, res.verdict)
        self.publish(res.check_id, "approval", {
            "issued": approval is not None, "request_hash": request_hash(tx),
            "approval_id": approval.approval_id if approval else None,
            "expiry": approval.expiry if approval else None,
            "why": "verdict ALLOW" if approval else f"verdict {res.verdict.verdict.value}: no approval"})
        signed = self.signer.sign(tx, approval)
        self.publish(res.check_id, "signer", {"signed": signed.signed, "reason": signed.reason})
        return {"check": check_payload(res), "approval": approval.model_dump() if approval else None,
                "signer": signed.to_dict()}


def create_app(services: Services | None = None) -> FastAPI:
    svc = services or Services.default()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async def delay_worker():
            while True:
                try:
                    await asyncio.to_thread(svc.holds.release_due)
                except Exception:  # noqa: BLE001 - keep the worker alive; holds stay pending
                    log.exception("DELAY worker failed")
                await asyncio.sleep(svc.settings.delay_check_seconds)

        async def warm_up():
            runner = svc.guard.runner
            if getattr(runner, "pool", None) is not None:
                await asyncio.to_thread(runner.start_pool)  # warm mode: fill the pool
            elif hasattr(runner, "warm_up"):
                secs = await asyncio.to_thread(runner.warm_up)  # fresh mode: warm Docker up
                log.info("sandbox warm-up took %.1fs", secs)

        tasks = [asyncio.create_task(delay_worker()), asyncio.create_task(warm_up())]
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            if getattr(svc.guard.runner, "pool", None) is not None:
                svc.guard.runner.pool.shutdown()

    app = FastAPI(title="Fail Closed", version="0.3.0", lifespan=lifespan,
                  description="Signing Guard: no approval, no signature.")
    app.state.services = svc
    app.add_middleware(CORSMiddleware, allow_origins=svc.settings.cors_origins, allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type", "X-Admin-Token"], allow_credentials=False)

    limiter = RateLimiter()
    app.state.limiter = limiter

    def limit(request: Request, what: str, n: int, window: float, words: str) -> None:
        if svc.settings.public_demo:
            limiter.hit(what, client_ip(request), n, window, words)

    def admin_in_public_demo(token: str | None) -> None:
        """Public demo: the raw API on the Guard's own state is for the operator only."""
        if svc.settings.public_demo:
            require_admin(token)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "llm_mode": svc.guard.explainer.mode, "model": svc.settings.vultr_model,
                "sandbox_image": svc.settings.sandbox_image}

    @app.get("/status")
    def status() -> dict:
        """For the UI header: what is running, and the main breaker level (0-4)."""
        level = level_of(svc.breaker.state())
        return {"sandbox_mode": svc.settings.sandbox_mode, "llm": "MOCK" if svc.guard.explainer.mode == "mock"
                else "REAL" if svc.guard.explainer.mode == "live" else "OFF", "model": svc.settings.vultr_model,
                "breaker_level": level, "redteam_public": svc.settings.redteam_public,
                "fast_path_llm_escalation": svc.settings.fast_path_llm_escalation,
                "delay_minutes": svc.settings.delay_minutes,
                "public_demo": svc.settings.public_demo,
                "demo_idle_reset_minutes": svc.settings.demo_idle_reset_minutes,
                "llm_budget": getattr(svc.guard.explainer, "budget", None).snapshot()
                if getattr(svc.guard.explainer, "budget", None) is not None else None}

    @app.get("/policies")
    def policies() -> dict:
        """Read-only: the active rules and thresholds, in plain words, for the Policies page.
        It reports the current settings; it decides nothing."""
        st = svc.settings
        eth = lambda wei: f"{wei / 10**18:g} ETH"  # noqa: E731
        return {
            "rules": [
                {"id": "R0", "verdict": "BLOCK", "text": "If the sandbox check fails, times out or returns nothing, the request is blocked."},
                {"id": "R1", "verdict": "BLOCK", "text": "A wallet may not delegate its control to a contract that is not on the approved list."},
                {"id": "R2", "verdict": "BLOCK", "text": "No more than one delegation authorization per request, and none for consecutive nonces."},
                {"id": "R3", "verdict": "NEEDS_HUMAN / BLOCK", "text": f"A withdrawal above {st.r3_max_single_dest_pct:g}% of the wallet needs review; a hidden drain seen in the sandbox is blocked."},
                {"id": "R4", "verdict": "NEEDS_HUMAN", "text": f"Treasury payments above {eth(st.r4_treasury_limit_wei)} need review."},
                {"id": "R5", "verdict": "NEEDS_HUMAN / BLOCK", "text": "Treasury payments go only to approved destinations; unknown and above the limit is blocked."},
                {"id": "R6", "verdict": "BLOCK", "text": f"A hot wallet may send at most {eth(st.r6_outflow_limit_wei)} per {st.r6_window_minutes} minutes."},
                {"id": "R7", "verdict": "DELAY / BLOCK", "text": f"One address receiving from {st.r7_delay_chains} chains within {st.r7_window_hours} h is held; from {st.r7_freeze_chains} chains it is frozen."},
                {"id": "R8", "verdict": "BLOCK", "text": f"{st.r8_freeze_assets} different assets to one address within {st.r8_window_minutes} min freezes that address."},
                {"id": "C1", "verdict": "NEEDS_HUMAN", "text": "Customer wallets only sign plain transfers; anything else needs review."},
            ],
            "breaker": [
                {"level": 1, "text": "Freeze one destination address (from R7 or R8)."},
                {"level": 2, "text": "Pause one wallet after a delegation or drain is detected (R1, R2, R3 with evidence)."},
                {"level": 3, "text": f"Safe mode on a chain after {st.safe_mode_paused_wallets} paused wallets or {st.safe_mode_evidence_blocks} evidence blocks within {st.safe_mode_window_minutes} min."},
                {"level": 4, "text": f"Global stop only with {st.global_stop_signals} independent signals."},
            ],
            "hold_minutes": st.delay_minutes,
            "lists": {"known_tokens": len(st.known_tokens), "delegate_allowlist": len(st.delegate_allowlist),
                      "treasury_allowlist": len(st.treasury_allowlist)},
            "sandbox": {"mode": st.sandbox_mode, "network": "none", "memory": st.sandbox_memory,
                        "cpus": st.sandbox_cpus, "processes": st.sandbox_pids,
                        "timeout_seconds": st.sandbox_timeout_seconds, "max_concurrent": st.sandbox_max_concurrent},
            "ai": {"escalation_on_fast_path": st.fast_path_llm_escalation,
                   "fast_path_timeout_seconds": st.fast_path_llm_timeout_seconds,
                   "ask_above_pct": st.ai_check_min_pct, "max_concurrent": st.llm_concurrency,
                   "policy": (f"A destination this wallet never paid before goes straight to full simulation. "
                              f"For a known destination the AI is asked only about an amount above "
                              f"{st.ai_check_min_pct:g}% of the wallet balance. At most {st.llm_concurrency} AI calls "
                              f"at a time, checks before explanations; no answer within "
                              f"{st.fast_path_llm_timeout_seconds:g} s means full simulation."),
                   "fast_model": st.vultr_model, "explain_model": st.vultr_explain_model or st.vultr_model},
        }

    @app.get("/guard/public-key")
    def public_key() -> dict:
        return {"algorithm": "ed25519", "public_key": svc.approver.public_key_hex, "key_id": svc.approver.key.key_id}

    @app.post("/check")
    def check(tx: TxRequest, x_admin_token: str | None = Header(default=None)) -> dict:
        """Dry run: the verdict and why. NEVER an approval (approvals only via /withdraw)."""
        admin_in_public_demo(x_admin_token)
        try:
            return {**check_payload(svc.guard.check(tx)), "dry_run": True}
        except GuardBusy as e:
            raise HTTPException(status_code=503, detail=f"Guard busy: {e}")

    @app.post("/withdraw")
    def withdraw(tx: TxRequest, x_admin_token: str | None = Header(default=None)) -> dict:
        admin_in_public_demo(x_admin_token)
        try:
            return svc.withdraw(tx)
        except GuardBusy as e:
            raise HTTPException(status_code=503, detail=f"Guard busy: {e}. Nothing was approved or signed.")

    @app.get("/history")
    def history(limit: int = Query(50, ge=1, le=500)) -> list[dict]:
        return svc.history.recent(limit)

    @app.get("/breaker")
    def breaker() -> dict:
        return {**svc.breaker.state(), "signals": svc.breaker.signals(), "events": svc.history.events()[-50:]}

    def require_admin(token: str | None) -> None:
        expected = svc.settings.admin_token
        if not token:
            raise HTTPException(status_code=401, detail="X-Admin-Token header required")
        if not expected or not secrets.compare_digest(token.encode(), expected.encode()):
            raise HTTPException(status_code=403, detail="invalid admin token (or admin actions disabled)")

    @app.get("/pending")
    def pending() -> list[dict]:
        """Held requests waiting: NEEDS_HUMAN (for a human) and DELAY (for the worker)."""
        return svc.history.held("pending")

    @app.post("/pending/{hold_id}/approve")
    def approve(hold_id: str, body: DecisionRequest | None = None,
                x_admin_token: str | None = Header(default=None)) -> dict:
        require_admin(x_admin_token)
        try:
            return svc.holds.approve(hold_id, actor=(body or DecisionRequest()).actor)
        except HoldError as e:
            raise HTTPException(status_code=e.status, detail=e.detail)

    @app.post("/pending/{hold_id}/reject")
    def reject(hold_id: str, body: DecisionRequest | None = None,
               x_admin_token: str | None = Header(default=None)) -> dict:
        require_admin(x_admin_token)
        try:
            return svc.holds.reject(hold_id, actor=(body or DecisionRequest()).actor)
        except HoldError as e:
            raise HTTPException(status_code=e.status, detail=e.detail)

    @app.post("/redteam/run")
    async def redteam_run(request: Request, x_admin_token: str | None = Header(default=None)):
        """Hostile payloads in real sandboxes (same limits as a check). SSE: one event per payload."""
        if not svc.settings.redteam_public:
            require_admin(x_admin_token)
        if not svc.redteam_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="a red-team run is already running")
        run_id = "redteam-" + uuid.uuid4().hex[:8]
        health_url = str(request.base_url).rstrip("/") + "/health"
        probe = svc.health_probe or (lambda: http_health(health_url))
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def on_result(r: dict) -> None:
            ev = {k: r[k] for k in ("name", "tried", "limit", "result", "status", "seconds", "health")}
            ev["contained"] = "yes" if r["contained"] else "no"
            svc.bus.publish("-", "redteam_payload", ev, run_id=run_id)
            loop.call_soon_threadsafe(queue.put_nowait, ("redteam_payload", ev))

        def work() -> None:
            try:
                report = svc.make_redteam(probe).run(on_result=on_result)
                report["run_id"] = run_id
                svc.history.save_redteam(run_id, report["finished"], report)
                svc.breaker.evaluate_global(report["finished"])  # a RED payload is a level-4 signal
                done = {"run_id": run_id, "all_contained": report["all_contained"], "red": report["red"]}
                svc.bus.publish("-", "redteam_finished", done, run_id=run_id)
                loop.call_soon_threadsafe(queue.put_nowait, ("redteam_finished", done))
            except Exception as e:  # noqa: BLE001 - a broken run is reported, never "contained"
                log.exception("red-team run failed")
                loop.call_soon_threadsafe(queue.put_nowait, ("redteam_error", {"error": f"{type(e).__name__}: {e}"}))
            finally:
                svc.redteam_lock.release()

        threading.Thread(target=work, name=run_id, daemon=True).start()

        async def gen():
            yield f"event: redteam_started\ndata: {json.dumps({'run_id': run_id})}\n\n"
            while True:
                kind, data = await queue.get()
                yield f"event: {kind}\ndata: {json.dumps(data)}\n\n"
                if kind in ("redteam_finished", "redteam_error"):
                    return

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/loadtest/run")
    async def loadtest_run(request: Request, normal: int = Query(50, ge=10, le=100), seed: int = Query(7)):
        limit(request, "simulation", svc.settings.rate_simulations_per_10_minutes, 600, "simulations")
        """The small load demo for the UI: N normal + 1 hidden attack, on its OWN isolated state
        (in-memory history and breaker), through the host's sandbox gate. SSE: one event per
        request, then the summary. Mock LLM numbers are labelled as such."""
        if not svc.loadtest_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="a load demo is already running")
        run_id = "loadtest-" + uuid.uuid4().hex[:8]
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        cfg = {"sandbox_mode": svc.settings.sandbox_mode,
               "fast_path_llm_escalation": svc.settings.fast_path_llm_escalation}

        def on_result(row: dict) -> None:
            svc.bus.publish("-", "loadtest_request", row, run_id=run_id)
            loop.call_soon_threadsafe(queue.put_nowait, ("loadtest_request", row))

        def work() -> None:
            try:
                r = run_config("api-demo", cfg, svc.settings, n_normal=normal, seed=seed,
                               on_result=on_result, gate=svc.guard.gate, runner=svc.loadtest_runner)
                r["run_id"] = run_id
                svc.bus.publish("-", "loadtest_finished", r, run_id=run_id)
                loop.call_soon_threadsafe(queue.put_nowait, ("loadtest_finished", r))
            except Exception as e:  # noqa: BLE001
                log.exception("load demo failed")
                loop.call_soon_threadsafe(queue.put_nowait, ("loadtest_error", {"error": f"{type(e).__name__}: {e}"}))
            finally:
                svc.loadtest_lock.release()

        threading.Thread(target=work, name=run_id, daemon=True).start()

        async def gen():
            yield f"event: loadtest_started\ndata: {json.dumps({'run_id': run_id, 'normal': normal, 'attacks': 1, 'config': cfg, 'llm': 'mock'})}\n\n"
            while True:
                kind, data = await queue.get()
                yield f"event: {kind}\ndata: {json.dumps(data)}\n\n"
                if kind in ("loadtest_finished", "loadtest_error"):
                    return

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/redteam/last")
    def redteam_last() -> dict:
        last = svc.history.last_redteam()
        if last is None:  # a normal state, not an error
            return {"available": False}
        return {"available": True, **last}

    @app.post("/breaker/reset")
    def reset(body: ResetRequest, x_admin_token: str | None = Header(default=None)) -> dict:
        require_admin(x_admin_token)
        now, actor = time.time(), body.actor
        if body.address:
            ok = svc.breaker.reset_destination(body.address, actor=actor, now=now)
            target: dict = {"address": body.address.lower()}
        elif body.wallet:
            ok = svc.breaker.reset_wallet(body.wallet, actor=actor, now=now)
            target = {"wallet": body.wallet.lower()}
        elif body.chain_id is not None:
            ok = svc.breaker.reset_safe_mode(body.chain_id, actor=actor, now=now)
            target = {"chain_id": body.chain_id}
        else:
            ok = svc.breaker.reset_global(actor=actor, now=now)
            target = {"global_stop": True}
        return {"reset": ok, **target, "detail": "reset and logged" if ok else "nothing to reset"}

    @app.post("/breaker/reset-all")
    def reset_all(x_admin_token: str | None = Header(default=None)) -> dict:
        """Back to level 0 Normal (admin): lift the global stop, end safe modes, unpause wallets,
        unfreeze destinations. Every reset is logged."""
        require_admin(x_admin_token)
        done = svc.breaker.reset_all(actor="api-admin", now=time.time())
        st = svc.breaker.state()
        return {"reset": done, "breaker": st, "level": level_of(st)}

    @app.get("/events")
    def events(after: int = 0, check_id: str | None = None, run_id: str | None = None) -> list[dict]:
        return svc.bus.since(after, check_id, run_id)

    @app.get("/stream")
    async def stream(limit: int | None = Query(None, ge=1), replay: int = Query(0, ge=0, le=1000),
                     run_id: str | None = None):
        """SSE. `replay` sends the last N buffered events first; `limit` closes after N events;
        `run_id` keeps only one demo run (or "main")."""
        queue = svc.bus.subscribe()
        backlog = [e for e in list(svc.bus.recent) if run_id is None or e["run_id"] == run_id]
        backlog = backlog[-replay:] if replay else []

        async def gen():
            sent = 0
            try:
                yield ": fail-closed stream\n\n"
                for ev in backlog:
                    yield f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {json.dumps(ev)}\n\n"
                    sent += 1
                    if limit and sent >= limit:
                        return
                while True:
                    try:
                        ev = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    if run_id is not None and ev["run_id"] != run_id:
                        continue
                    yield f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {json.dumps(ev)}\n\n"
                    sent += 1
                    if limit and sent >= limit:
                        return
            finally:
                svc.bus.unsubscribe(queue)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    def _scenario_files() -> dict[str, Path]:
        return {p.stem: p for p in discover(svc.scenarios_dir)}

    @app.get("/scenarios")
    def scenarios() -> list[dict]:
        out = []
        for name, path in _scenario_files().items():
            sc = load(path)
            out.append({"name": name, "title": sc.title, "description": sc.description, "labels": sc.labels,
                        "requests": [{"label": r.label, "plain": r.plain or r.label, "expect": r.expect.model_dump()}
                                     for r in sc.requests]})
        return out

    @app.post("/scenarios/{name}/run")
    def run_scenario(request: Request, name: str, run_id: str | None = None) -> dict:
        """`run_id` may be chosen by the client (so it can subscribe to /stream?run_id first)."""
        limit(request, "replay", svc.settings.rate_replays_per_minute, 60, "replays")
        files = _scenario_files()
        if name not in files:  # only known names: no paths from the client
            raise HTTPException(status_code=404, detail="unknown scenario")
        if run_id is not None and not RUN_ID.match(run_id):
            raise HTTPException(status_code=422, detail="run_id: 8-32 lowercase letters or digits")
        run_id = run_id or uuid.uuid4().hex[:12]
        with svc.demo_lock:
            if run_id in svc.demo_runs:
                raise HTTPException(status_code=409, detail="run_id already used")
        sc = load(files[name])
        iso = svc.isolated(run_id)  # fresh state per run: one visitor never changes another's result
        svc.remember_demo(run_id, iso)
        iso.publish("-", "run_started", {"scenario": name, "requests": len(sc.requests)})
        results = []
        try:
            for r in sc.requests:
                if r.is_action:  # a scripted human reset, on this run's own breaker
                    done = r.apply(iso.breaker, time.time())
                    iso.publish("-", "human_action", {"action": r.action, "target": r.target, "by": r.actor,
                                                      "done": done})
                    st = iso.breaker.state()
                    results.append({"label": r.label, "plain": r.plain or r.label, "expect": r.expect.model_dump(),
                                    "action": r.action, "target": r.target, "by": r.actor, "done": done,
                                    "breaker": st})
                    iso.publish("-", "breaker_state", {**st, "level": level_of(st)})
                    continue
                out = iso.withdraw(r.tx())
                st = iso.breaker.state()
                results.append({"label": r.label, "plain": r.plain or r.label, "expect": r.expect.model_dump(),
                                **out, "breaker_state": st})
                iso.publish("-", "breaker_state", {**st, "level": level_of(st)})  # the UI colour, step by step
        except GuardBusy as e:
            raise HTTPException(status_code=503, detail=f"Guard busy: {e}. Nothing was approved or signed.")
        # Demo: fast-forward the clock past the DELAY so the visitor sees what the worker decides.
        later = time.time() + svc.settings.delay_minutes * 60 + 1
        released = iso.holds.release_due(now=later)
        iso.publish("-", "run_finished", {"scenario": name, "delay_worker": len(released)})
        return {"name": name, "title": sc.title, "run_id": run_id, "results": results,
                "delay_worker": {"ran_at": f"+{svc.settings.delay_minutes} min (simulated)",
                                 "skipped_minutes": svc.settings.delay_minutes, "results": released},
                "breaker": iso.breaker.state(), "blast_radius": blast_radius_data(iso.history, sc),
                "pending": iso.history.held("pending", "needs_human")}

    # ---- the visitor's own demo run: human approval without touching the main state ------

    @app.get("/demo/{run_id}/pending")
    def demo_pending(run_id: str) -> list[dict]:
        return svc.demo(run_id).history.held("pending", "needs_human")

    @app.post("/demo/{run_id}/breaker/reset-all")
    def demo_reset_all(run_id: str, x_admin_token: str | None = Header(default=None)) -> dict:
        """Lift a replay's breaker back to level 0 (the level-4 banner's action). Still an admin
        action: the admin token is needed, even in a visitor's own replay."""
        require_admin(x_admin_token)
        iso = svc.demo(run_id)
        done = iso.breaker.reset_all(actor="api-admin", now=time.time())
        st = iso.breaker.state()
        iso.publish("-", "breaker_state", {**st, "level": level_of(st)})
        return {"reset": done, "breaker": st, "level": level_of(st)}

    @app.post("/demo/reset")
    def demo_reset(request: Request, body: DemoReset) -> dict:
        """Public "Reset demo": drop the visitor's own runs (breaker, queue, history), back to
        level 0. Run ids are random, so nobody can reset another visitor's demo by guessing.
        The Guard's own state is not touched (lifting a real level needs the admin token)."""
        limit(request, "demo_action", svc.settings.rate_demo_actions_per_minute, 60, "demo actions")
        return {"reset": svc.forget_demos(body.run_ids), "level": 0}

    @app.post("/demo/{run_id}/pending/{hold_id}/{decision}")
    def demo_decide(request: Request, run_id: str, hold_id: str, decision: str) -> dict:
        limit(request, "demo_action", svc.settings.rate_demo_actions_per_minute, 60, "demo actions")
        if decision not in ("approve", "reject"):
            raise HTTPException(status_code=404, detail="unknown decision")
        iso = svc.demo(run_id)
        try:
            if decision == "approve":
                return iso.holds.approve(hold_id, actor="demo-visitor")
            return iso.holds.reject(hold_id, actor="demo-visitor")
        except HoldError as e:
            raise HTTPException(status_code=e.status, detail=e.detail)

    # ---- the built web UI (frontend/dist), after every API route ---------------------------
    if FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="ui")

    return app


def __getattr__(name: str):
    # `uvicorn app.api:app` builds the real app lazily (tests import create_app instead)
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
