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
import json
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
from pydantic import BaseModel, model_validator

from app.config import REPO_ROOT, Settings, get_settings
from app.models import TxRequest
from approval import Approval, Approver, GuardKey, request_hash
from breaker import Breaker
from events import EventBus
from guard import CheckResult, Guard
from history import History
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


class ResetRequest(BaseModel):
    address: str | None = None
    wallet: str | None = None

    @model_validator(mode="after")
    def _one(self) -> "ResetRequest":
        if (self.address is None) == (self.wallet is None):
            raise ValueError("give exactly one of address or wallet")
        return self


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
        "timings_ms": {k: round(t * 1000, 1) for k, t in res.timings.items()},
    }


class Services:
    """Everything the API needs; built once, injectable for tests."""

    def __init__(self, settings: Settings, guard: Guard, history: History, approver: Approver,
                 signer: MockSigner, bus: EventBus, scenarios_dir: Path, run_id: str = "main"):
        self.settings, self.guard, self.history = settings, guard, history
        self.approver, self.signer, self.bus, self.scenarios_dir = approver, signer, bus, scenarios_dir
        self.run_id = run_id
        self.breaker = Breaker(history)
        self.holds = HoldManager(guard, approver, signer, publish=self.publish)
        self.redteam_lock = threading.Lock()  # one red-team run at a time
        self.health_probe: Any = None  # override in tests; default: HTTP GET of our own /health

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
                      breaker=Breaker(history), clock=g.clock, perf=g.perf,
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
                      SandboxRunner(settings), LLMExplainer(settings), breaker=Breaker(history),
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

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "llm_mode": svc.guard.explainer.mode, "model": svc.settings.vultr_model,
                "sandbox_image": svc.settings.sandbox_image}

    @app.get("/guard/public-key")
    def public_key() -> dict:
        return {"algorithm": "ed25519", "public_key": svc.approver.public_key_hex, "key_id": svc.approver.key.key_id}

    @app.post("/check")
    def check(tx: TxRequest) -> dict:
        """Dry run: the verdict and why. NEVER an approval (approvals only via /withdraw)."""
        try:
            return {**check_payload(svc.guard.check(tx)), "dry_run": True}
        except GuardBusy as e:
            raise HTTPException(status_code=503, detail=f"Guard busy: {e}")

    @app.post("/withdraw")
    def withdraw(tx: TxRequest) -> dict:
        try:
            return svc.withdraw(tx)
        except GuardBusy as e:
            raise HTTPException(status_code=503, detail=f"Guard busy: {e}. Nothing was approved or signed.")

    @app.get("/history")
    def history(limit: int = Query(50, ge=1, le=500)) -> list[dict]:
        return svc.history.recent(limit)

    @app.get("/breaker")
    def breaker() -> dict:
        return {"frozen_destinations": svc.history.frozen(), "paused_wallets": svc.history.paused(),
                "events": svc.history.events()[-50:]}

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

    @app.get("/redteam/last")
    def redteam_last() -> dict:
        last = svc.history.last_redteam()
        if last is None:
            raise HTTPException(status_code=404, detail="no red-team run yet")
        return last

    @app.post("/breaker/reset")
    def reset(body: ResetRequest, x_admin_token: str | None = Header(default=None)) -> dict:
        require_admin(x_admin_token)
        now = time.time()
        if body.address:
            ok = svc.breaker.reset_destination(body.address, actor="api-admin", now=now)
            target = {"address": body.address.lower()}
        else:
            ok = svc.breaker.reset_wallet(body.wallet, actor="api-admin", now=now)
            target = {"wallet": body.wallet.lower()}
        return {"reset": ok, **target, "detail": "reset and logged" if ok else "nothing to reset"}

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
            out.append({"name": name, "title": sc.title, "description": sc.description,
                        "requests": [{"label": r.label, "expect": r.expect.model_dump()} for r in sc.requests]})
        return out

    @app.post("/scenarios/{name}/run")
    def run_scenario(name: str) -> dict:
        files = _scenario_files()
        if name not in files:  # only known names: no paths from the client
            raise HTTPException(status_code=404, detail="unknown scenario")
        sc = load(files[name])
        run_id = uuid.uuid4().hex[:12]
        iso = svc.isolated(run_id)  # fresh state per run: one visitor never changes another's result
        iso.publish("-", "run_started", {"scenario": name, "requests": len(sc.requests)})
        results = []
        try:
            for r in sc.requests:
                out = iso.withdraw(r.tx())
                results.append({"label": r.label, "expect": r.expect.model_dump(), **out})
        except GuardBusy as e:
            raise HTTPException(status_code=503, detail=f"Guard busy: {e}. Nothing was approved or signed.")
        # Demo: fast-forward the clock past the DELAY so the visitor sees what the worker decides.
        later = time.time() + svc.settings.delay_minutes * 60 + 1
        released = iso.holds.release_due(now=later)
        iso.publish("-", "run_finished", {"scenario": name, "delay_worker": len(released)})
        return {"name": name, "title": sc.title, "run_id": run_id, "results": results,
                "delay_worker": {"ran_at": f"+{svc.settings.delay_minutes} min (simulated)", "results": released},
                "breaker": {"frozen_destinations": iso.history.frozen(), "paused_wallets": iso.history.paused()}}

    return app


def __getattr__(name: str):
    # `uvicorn app.api:app` builds the real app lazily (tests import create_app instead)
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
