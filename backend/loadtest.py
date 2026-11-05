"""Load demo: 500 normal withdrawals + 1 hidden Binance-style attack, through the real Guard.

Every request takes the full /withdraw path: Guard check -> approval (ALLOW only) -> mock signer.
Measured: attacks stopped, normal requests NOT allowed (by verdict and rule; goal 0),
latency p50/p95/p99 per path, throughput, total time, sandboxes started.

Workload (seeded, reproducible):
- 3 customer hot wallets; ~55% native ETH, ~45% transfers of 3 MockTokens
- chains 1 / 10 / 42161 / 137; amounts log-normal (ETH ~0.3, tokens ~500 units)
- destinations: ~90% returning customers (each tied to one wallet and one chain, paid before:
  the run starts with one earlier payout per returning customer in its history, a week back),
  ~10% (NEW_DEST_SHARE) addresses the wallet never paid, so the AI escalation policy is used
- spread over 10 simulated hours (the history windows see realistic timing)
- the attack: a 4th hot wallet is asked to sign 3 EIP-7702 authorizations to a sweeper,
  hidden at a random position. It is a wallet that serves no customers in this run, so the
  latched level-2 pause it triggers does not hide false positives on the other wallets.

LLM: the local mock by default (MOCK_LLM_LATENCY_MS per call, MOCK_LLM_ESCALATE_RATE of plain
transfers escalated; all numbers then say "mock LLM"), or the real Vultr model with --llm live
(calls, failures, tokens and estimated cost are reported; the key is never printed).
"""

import json
import math
import os
import platform
import random
import statistics
import threading
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import Settings
from app.models import Decision, TxRequest
from approval import Approver, GuardKey
from breaker import Breaker
from guard import Guard
from history import CheckRecord, History
from llm import LLMExplainer
from sandbox_gate import GuardBusy, SandboxGate
from sandbox_runner import SandboxRunner
from signer_mock import MockSigner
from state_provider import StartingState, StaticStateProvider

ETH = 10**18
T0 = 1_900_000_000.0
HOT = [f"0x10000000000000000000000000000000000001{i:02x}" for i in (1, 2, 3)]
ATTACKED = "0x1000000000000000000000000000000000000104"
SWEEPER = "0x00000000000000000000000000000000005eeeee"
TOKENS = {"0x70c00000000000000000000000000000000000a1": "mUSDC",
          "0x70c00000000000000000000000000000000000a2": "mUSDT",
          "0x70c00000000000000000000000000000000000a3": "mDAI"}
CHAINS, CHAIN_WEIGHTS = [1, 10, 42161, 137], [0.5, 0.2, 0.2, 0.1]
ATTACK_NONCE = 1000

CONFIGS = {
    "fresh/escalation-on": {"sandbox_mode": "fresh", "fast_path_llm_escalation": True},
    "fresh/escalation-off": {"sandbox_mode": "fresh", "fast_path_llm_escalation": False},
    "warm/escalation-on": {"sandbox_mode": "warm", "fast_path_llm_escalation": True},
    "warm/escalation-off": {"sandbox_mode": "warm", "fast_path_llm_escalation": False},
}


@dataclass
class Item:
    index: int
    t: float  # simulated seconds after T0
    tx: TxRequest
    attack: bool
    kind: str
    new_dest: bool = False  # a destination this wallet never paid before (the AI check is asked)


def _addr(rng: random.Random) -> str:
    return "0x" + "".join(rng.choice("0123456789abcdef") for _ in range(40))


def build_workload(n_normal: int = 500, seed: int = 7, hours: float = 10.0,
                   new_dest_share: float = 0.1) -> tuple[list[Item], StartingState, dict]:
    rng = random.Random(seed)
    regulars = [(_addr(rng), rng.choices(CHAINS, CHAIN_WEIGHTS)[0], rng.choice(HOT)) for _ in range(200)]
    times = sorted(rng.uniform(0, hours * 3600) for _ in range(n_normal))
    items: list[Item] = []
    for i, t in enumerate(times):
        new = rng.random() < new_dest_share
        if new:
            dest, chain, wallet = _addr(rng), rng.choices(CHAINS, CHAIN_WEIGHTS)[0], rng.choice(HOT)
        else:
            dest, chain, wallet = rng.choice(regulars)
        if rng.random() < 0.55:
            wei = int(min(8.0, max(0.005, rng.lognormvariate(math.log(0.3), 1.0))) * ETH)
            body = {"to": dest, "value": str(wei), "data": "0x"}
            kind = "native"
        else:
            token = rng.choice(list(TOKENS))
            units = int(min(50_000, max(10, rng.lognormvariate(math.log(500), 1.2))))
            data = "0xa9059cbb" + dest[2:].rjust(64, "0") + format(units * ETH, "064x")
            body = {"to": token, "value": "0", "data": data}
            kind = TOKENS[token]
        tx = TxRequest.model_validate({"wallet_type": "customer_hot", "chain_id": chain, "from": wallet, **body})
        items.append(Item(i, t, tx, False, kind, new))

    pos = rng.randrange(1, n_normal)
    t_attack = (items[pos - 1].t + items[pos].t) / 2
    attack = TxRequest.model_validate({
        "wallet_type": "customer_hot", "chain_id": 1, "from": ATTACKED, "to": ATTACKED, "value": "0",
        "data": "0x", "authorization_list": [{"chain_id": 1, "address": SWEEPER, "nonce": n}
                                             for n in range(ATTACK_NONCE - 1, ATTACK_NONCE + 2)]})
    items.insert(pos, Item(pos, t_attack, attack, True, "eip7702-attack"))
    for i, it in enumerate(items):
        it.index = i

    accounts: dict[str, Any] = {w: {"balance": str(20_000 * ETH), "nonce": 50_000,
                                     "token_balances": {t: str(10**9 * ETH) for t in TOKENS}} for w in HOT}
    accounts[ATTACKED] = {"balance": str(3_000 * ETH), "nonce": ATTACK_NONCE}
    accounts[SWEEPER] = {"code": "artifact:MockSweeper"}
    for t in TOKENS:
        accounts[t] = {"code": "artifact:MockToken"}
    policy = {"known_tokens": list(TOKENS)}
    return items, StartingState.model_validate({"accounts": accounts}), policy


def dest_of(it: Item) -> str:
    return it.tx.to if it.kind == "native" else "0x" + it.tx.data[34:74]


def seed_known_payouts(history: History, items: list[Item]) -> int:
    """One earlier small payout (a week before the run, outside every rule window) per returning
    customer, so "a destination this wallet paid before" means the same as in production."""
    known = sorted({(it.tx.from_, dest_of(it), it.tx.chain_id) for it in items if not it.attack and not it.new_dest})
    for n, (wallet, dest, chain) in enumerate(known):
        history.record_check(CheckRecord(
            id=f"seed-{n}", ts=T0 - 7 * 86400, wallet=wallet, chain_id=chain, wallet_type="customer_hot",
            kind="native_transfer", destination=dest, asset="native", amount=10**16, path="fast",
            path_reasons=["earlier payout (load-test history seed)"], verdict="ALLOW", findings=[], timings={},
            llm=None, request={}))
    return len(known)


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 1)


def machine_info() -> dict[str, Any]:
    info = {"os": platform.platform(), "python": platform.python_version(), "cpus": os.cpu_count(),
            "processor": platform.processor() or platform.machine()}
    try:
        import docker

        d = docker.from_env().info()
        info.update({"docker": d.get("ServerVersion"), "docker_os": d.get("OperatingSystem"),
                     "docker_cpus": d.get("NCPU"), "docker_mem_gib": round((d.get("MemTotal") or 0) / 2**30, 1)})
    except Exception as e:  # noqa: BLE001
        info["docker"] = f"unavailable ({type(e).__name__})"
    return info


def run_config(name: str, overrides: dict, base: Settings, n_normal: int = 500, seed: int = 7,
               concurrency: int = 8, on_result: Callable[[dict], None] | None = None,
               runner: Any = None, items_state_policy=None, gate: SandboxGate | None = None,
               llm_live: bool = False) -> dict[str, Any]:
    """Run one config on its own isolated state (in-memory history, its own breaker)."""
    items, state, policy = items_state_policy or build_workload(n_normal, seed)
    s = Settings(**{**base.model_dump(), **policy, **overrides, "llm_mode": "live" if llm_live else "mock",
                    "history_db": ":memory:"})
    history = History(":memory:")
    seed_known_payouts(history, items)
    runner = runner or SandboxRunner(s)
    gate = gate or SandboxGate(s.sandbox_max_concurrent, s.sandbox_queue_seconds)
    explainer = LLMExplainer(s)
    guard = Guard(s, history, StaticStateProvider(state), runner, explainer, breaker=Breaker(history, s), gate=gate)
    key = GuardKey(Ed25519PrivateKey.generate())
    approver = Approver(key, s.approval_ttl_seconds)
    signer = MockSigner(key.public_key_hex)

    started_before = getattr(runner, "started", 0)  # count every container, the pool prefill too
    warm_up = 0.0
    if getattr(runner, "pool", None) is not None:
        t0 = time.perf_counter()
        runner.start_pool()
        warm_up = time.perf_counter() - t0

    rows: list[dict] = []
    lock = threading.Lock()

    def one(it: Item) -> None:
        t0 = time.perf_counter()
        try:
            res = guard.check(it.tx, now=T0 + it.t)
            approval = approver.issue(it.tx, res.verdict)
            signed = signer.sign(it.tx, approval).signed
            v, path = res.verdict.verdict.value, res.verdict.path.value if res.verdict.path else "-"
            rules = sorted({f.rule_id for f in res.verdict.findings if f.action is not Decision.ALLOW})
            reasons = res.verdict.path_reasons
            detail = "; ".join(f"[{f.rule_id}] {f.detail}"[:200] for f in res.verdict.findings
                               if f.action is not Decision.ALLOW)
            timing = {k: round(v * 1000) for k, v in res.timings.items() if isinstance(v, float)}
        except GuardBusy as e:
            v, path, rules, signed, reasons, detail, timing = "BUSY", "-", ["503"], False, [], str(e), {}
        row = {"i": it.index, "attack": it.attack, "kind": it.kind, "chain": it.tx.chain_id, "verdict": v,
               "new_dest": it.new_dest, "ai_asked": any(r.endswith(": AI check asked") for r in reasons),
               "path": path, "rules": rules, "signed": signed, "ms": round((time.perf_counter() - t0) * 1000, 1),
               "escalated": any("LLM asked" in r for r in reasons),
               "ai_unavailable": next((r[len("AI check unavailable ("):].split(") -> ")[0] for r in reasons
                                       if r.startswith("AI check unavailable")), None)}
        if v != "ALLOW":
            row["detail"] = detail
            row["timings_ms"] = timing
        with lock:
            rows.append(row)
            if on_result:
                on_result(row)

    t_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="load") as pool:
        list(pool.map(one, items))
    total = time.perf_counter() - t_start
    guard._explain_pool.shutdown(wait=True)  # background explanations: their calls and tokens count too
    if getattr(runner, "pool", None) is not None:
        pool_stats = dict(runner.pool.stats)
        runner.pool.shutdown()
    else:
        pool_stats = None

    normal = [r for r in rows if not r["attack"]]
    attack = [r for r in rows if r["attack"]]
    not_allowed = [r for r in normal if r["verdict"] != "ALLOW"]
    lat = {p: [r["ms"] for r in rows if r["path"] == p] for p in ("fast", "slow", "early")}
    return {
        "config": name, "overrides": overrides, "requests": len(rows), "normal": len(normal),
        "attacks": len(attack),
        "attacks_stopped": sum(1 for r in attack if r["verdict"] != "ALLOW" and not r["signed"]),
        "attack_result": attack[0] if attack else None,
        "normal_not_allowed": len(not_allowed),
        "normal_not_allowed_by_verdict": dict(Counter(r["verdict"] for r in not_allowed)),
        "normal_not_allowed_by_rule": dict(Counter(x for r in not_allowed for x in r["rules"])),
        "normal_not_allowed_rows": not_allowed,
        "normal_signed": sum(1 for r in normal if r["signed"]),
        "paths": dict(Counter(r["path"] for r in rows)),
        "escalated": sum(1 for r in rows if r["escalated"]),
        "new_destinations": sum(1 for r in normal if r["new_dest"]),
        "ai_asked": sum(1 for r in rows if r["ai_asked"]),
        "latency_ms": {p: {"n": len(v), "p50": _pct(v, .5), "p95": _pct(v, .95), "p99": _pct(v, .99),
                           "mean": round(statistics.mean(v), 1) if v else None} for p, v in lat.items()},
        "total_seconds": round(total, 2), "throughput_rps": round(len(rows) / total, 1),
        "concurrency": concurrency, "sandboxes_started": getattr(runner, "started", 0) - started_before,
        "sandbox_retries": getattr(runner, "retries", 0),
        "busy_503": sum(1 for r in rows if r["verdict"] == "BUSY"),
        "pool": pool_stats, "pool_warm_up_seconds": round(warm_up, 2),
        "ai_unavailable": sum(1 for r in rows if r["ai_unavailable"]),
        "ai_unavailable_by_reason": dict(Counter(r["ai_unavailable"] for r in rows if r["ai_unavailable"])),
        "llm": {"mode": explainer.mode, "model": s.vultr_model if explainer.mode == "live" else None,
                "reasoning_effort": s.llm_reasoning_effort if explainer.mode == "live" else None,
                "latency_ms": s.mock_llm_latency_ms if explainer.mode == "mock" else None,
                "escalate_rate": s.mock_llm_escalate_rate if explainer.mode == "mock" else None,
                **explainer.usage.snapshot()},
        "sandbox_timeout_seconds": s.sandbox_timeout_seconds,
    }


def markdown_table(results: list[dict], machine: dict) -> str:
    def lat(r, p):
        d = r["latency_ms"][p]
        return "-" if not d["n"] else f"{d['p50']:.0f} / {d['p95']:.0f} / {d['p99']:.0f} (n={d['n']})"

    llm = results[0]["llm"]
    live = llm["mode"] == "live"
    llm_line = (f"**Real LLM**: Vultr `{llm['model']}`, reasoning_effort {llm['reasoning_effort'] or 'default'}."
                if live else
                f"**Mock LLM**: {llm['latency_ms']} ms per call, escalates {llm['escalate_rate']:.0%} of plain transfers.")
    lines = [
        f"**{results[0]['normal']} normal + {results[0]['attacks']} attack**, concurrency {results[0]['concurrency']}. "
        f"Machine: {machine.get('os')}, {machine.get('cpus')} CPUs; Docker {machine.get('docker')} "
        f"({machine.get('docker_os')}, {machine.get('docker_cpus')} CPUs, {machine.get('docker_mem_gib')} GiB). "
        + llm_line,
        "",
        "| Config | Attack stopped | Normal not ALLOWed | Fast path p50 / p95 / p99 ms | Slow path p50 / p95 / p99 ms "
        "| Throughput | Total | Sandboxes started |"
        + (" New destinations | AI asked | Escalated | AI check unavailable | LLM calls (failed) | 429s | Cost |"
           if live else ""),
        "|---|---|---|---|---|---|---|---|" + ("---|---|---|---|---|---|---|" if live else ""),
    ]
    for r in results:
        na = r["normal_not_allowed"]
        why = ", ".join(f"{k} {v}" for k, v in r["normal_not_allowed_by_rule"].items()) if na else ""
        lines.append(
            f"| {r['config']} | {r['attacks_stopped']}/{r['attacks']} | {na}{f' ({why})' if why else ''} "
            f"| {lat(r, 'fast')} | {lat(r, 'slow')} | {r['throughput_rps']} req/s | {r['total_seconds']} s "
            f"| {r['sandboxes_started']} |" + (live_cells(r) if live else ""))
    return "\n".join(lines) + "\n"


def live_cells(r: dict) -> str:
    u = r["llm"]
    unav = r["ai_unavailable"]
    why = ", ".join(f"{k} {v}" for k, v in r["ai_unavailable_by_reason"].items())
    failed = sum(u["failures"].values())
    cost = f"${u['cost_usd']:.4f}" if u.get("cost_usd") is not None else "n/a"
    n429 = u["failures"].get("rate limited (429)", 0)
    return (f" {r['new_destinations']} | {r['ai_asked']} | {r['escalated']} | {unav}{f' ({why})' if why else ''} "
            f"| {u['calls']} ({failed}) | {n429} | {cost} |")


def run_all(base: Settings, out_dir: Path, n_normal: int = 500, seed: int = 7, concurrency: int = 8,
            configs: list[str] | None = None, progress: Callable[[str], None] = print,
            llm_live: bool = False, new_dest_share: float = 0.1) -> list[dict]:
    machine = machine_info()
    wl = build_workload(n_normal, seed, new_dest_share=new_dest_share)
    results = []
    for name in configs or list(CONFIGS):
        progress(f"running {name} ...")
        r = run_config(name, CONFIGS[name], base, n_normal, seed, concurrency, items_state_policy=wl,
                       llm_live=llm_live)
        u = r["llm"]
        progress(f"  done in {r['total_seconds']} s: attack {r['attacks_stopped']}/{r['attacks']}, "
                 f"normal not allowed {r['normal_not_allowed']}, escalated {r['escalated']}, "
                 f"AI unavailable {r['ai_unavailable']}, LLM calls {u['calls']}"
                 + (f", cost ${u['cost_usd']:.4f}" if u.get("cost_usd") is not None and llm_live else ""))
        results.append(r)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    payload = {"machine": machine, "seed": seed, "results": results, "created": stamp,
               "note": ("laptop, Docker Desktop, real Vultr model " + str(base.vultr_model) if llm_live
                        else "laptop, Docker Desktop, mock LLM") + "; to be re-run on the Vultr VM"}
    (out_dir / f"loadtest-{stamp}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md = f"# Load test {stamp}\n\n" + markdown_table(results, machine)
    (out_dir / f"loadtest-{stamp}.md").write_text(md, encoding="utf-8")
    (out_dir / "latest.md").write_text(md, encoding="utf-8")
    return results
