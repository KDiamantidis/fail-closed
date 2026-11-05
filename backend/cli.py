"""Fail Closed CLI.

    python backend/cli.py run-all [--dir scenarios] [--db data/run-all.sqlite3]
    python backend/cli.py scenario scenarios/d_binance_7702.json [--db ...]
    python backend/cli.py breaker [--db ...]
    python backend/cli.py reset --address 0x... | --wallet 0x... [--actor name] [--db ...]

run-all runs every scenario file in order through the real Guard (real sandbox, LLM as
configured: MOCK without a key) on one fresh history, prints one table and the breaker
state. `scenario` prints the full report for each request of one file.
"""

import argparse
import json
import logging
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import Settings, get_settings
from approval import Approver, GuardKey
from holds import HoldManager
from signer_mock import MockSigner
from app.models import Decision
from breaker import Breaker
from guard import CheckResult, Guard
from history import History
from llm import LLMExplainer
from sandbox_runner import SandboxRunner
from scenario import Scenario, ScenarioRequest, discover, load
from state_provider import ScenarioStateProvider

REPO = Path(__file__).resolve().parents[1]
DAY = 86_400.0
T0 = 1_800_000_000.0  # fixed scenario epoch: windows are deterministic
EMPTY = {"balance": "0x0", "nonce": 0, "code": "0x"}


# ---- formatting helpers ------------------------------------------------------------

def eth(wei: int) -> str:
    return f"{Decimal(wei) / Decimal(10**18):,.6f} ETH"


def as_int(v: Any) -> int:
    return int(v, 16) if isinstance(v, str) else int(v or 0)


def field_change(pre: dict, post: dict, field: str) -> tuple[Any, Any] | None:
    """diffMode: `pre` is the full prior account (zero nonce / empty code omitted),
    `post` lists only changed fields. Returns (before, after) if the field changed."""
    if field not in post:
        return None
    return pre.get(field, EMPTY[field]), post[field]


def auth_outcomes(auths: list[dict], start_nonce: int, chain_id: int) -> list[str]:
    """Replay EIP-7702 nonce handling: an auth applies only if its nonce equals the current one."""
    current, out = start_nonce, []
    for a in auths:
        n, cid = as_int(a.get("nonce", "0x0")), as_int(a.get("chainId", "0x0"))
        if cid not in (0, chain_id):
            out.append("skipped (wrong chain)")
        elif n == current:
            out.append("APPLIED (matches wallet nonce)")
            current += 1
        elif n < current:
            out.append(f"ignored (stale, wallet nonce {current})")
        else:
            out.append(f"ignored (future, wallet nonce {current})")
    return out


def lab(sc: Scenario, addr: str) -> str:
    name = sc.label(addr)
    return f"{addr} ({name})" if name else addr


def expect_status(r: ScenarioRequest, res: CheckResult | None, history: History | None = None) -> str:
    problems = []
    if history is not None:
        if r.expect.wallet_paused is not None and not r.is_action:
            paused = history.is_paused(r.tx().from_) is not None
            if paused != r.expect.wallet_paused:
                problems.append(f"wallet paused={paused}, expected {r.expect.wallet_paused}")
        if r.expect.safe_mode_chains is not None:
            chains = sorted(m["chain_id"] for m in history.safe_modes())
            if chains != sorted(r.expect.safe_mode_chains):
                problems.append(f"safe mode chains {chains} != {sorted(r.expect.safe_mode_chains)}")
        if r.expect.global_stop is not None and (history.global_stop() is not None) != r.expect.global_stop:
            problems.append(f"global stop {history.global_stop() is not None} != {r.expect.global_stop}")
    if res is None:  # an action step: only the breaker expectations apply
        return "OK" if not problems else "MISMATCH: " + "; ".join(problems)
    v = res.verdict
    if r.expect.verdict and v.verdict.value != r.expect.verdict:
        problems.append(f"verdict {v.verdict.value} != {r.expect.verdict}")
    if r.expect.path and (v.path.value if v.path else None) != r.expect.path:
        problems.append(f"path {v.path.value if v.path else None} != {r.expect.path}")
    missing = set(r.expect.rules) - {f.rule_id for f in v.findings}
    if missing:
        problems.append(f"missing {', '.join(sorted(missing))}")
    return "OK" if not problems else "MISMATCH: " + "; ".join(problems)


# ---- running -----------------------------------------------------------------------

def make_guard(sc: Scenario, base: Settings, history: History, clock: dict) -> Guard:
    s = sc.settings(base)
    return Guard(s, history, ScenarioStateProvider(sc.path), SandboxRunner(s), LLMExplainer(s),
                 breaker=Breaker(history, s), clock=lambda: clock["now"])


def fresh_history(db: str) -> History:
    p = Path(db)
    if p.exists():
        p.unlink()
    return History(p)


def run_file(sc: Scenario, guard: Guard, clock: dict, base_t: float, report: bool):
    """Run every request; each expectation is checked right after its own request."""
    results = []
    for i, r in enumerate(sc.requests, 1):
        clock["now"] = base_t + r.t
        if r.is_action:  # a human resetting the breaker
            done = r.apply(guard.breaker, clock["now"])
            status = expect_status(r, None, guard.history)
            results.append((r, None, status))
            if report:
                print_action(i, len(sc.requests), r, done, guard.history, status)
            continue
        res = guard.check(r.tx())
        status = expect_status(r, res, guard.history)
        results.append((r, res, status))
        if report:
            print_check(sc, i, r, res, guard, status)
    return results


def print_action(i: int, n: int, r: ScenarioRequest, done: bool, history: History, status: str) -> None:
    print("=" * 86)
    print(f" step {i}/{n}: HUMAN ACTION by {r.actor}: {r.label}")
    print("=" * 86)
    print(f"action  : {r.action}{f' {r.target}' if r.target is not None else ''} -> "
          f"{'done (logged)' if done else 'nothing to reset'}   ({status})")
    print_breaker(history)
    print("")


def blast_radius(history: History, sc: Scenario) -> list[str]:
    """For each frozen destination in this scenario: value that was ALLOWed to it before the
    freeze (and value held by DELAY, which the freeze keeps from being released)."""
    from reports import blast_radius_data

    lines = []
    for br in blast_radius_data(history, sc):
        lines.append(f"blast radius for {lab(sc, br['address'])}, frozen by {br['frozen_by']}:")
        for title, key in (("ALLOWED before the freeze (would have been signed)", "allowed"),
                           ("held by DELAY (the freeze is a new signal, so they are not released)", "held")):
            lines.append(f"  {title}:")
            rows, tot = br[key], br[f"{key}_totals"]
            if not rows:
                lines.append("    nothing")
                continue
            for chain in tot["chains"]:
                parts = [f"{r['units']} {r['asset_name']}" for r in rows if r["chain_id"] == chain]
                lines.append(f"    chain {chain:>6}: " + ", ".join(parts))
            lines.append(f"    total: {Decimal(tot['native_units']):f} ETH + {Decimal(tot['token_units']):,f} tokens "
                         f"({tot['token_types']} token types)")
    return lines


def run_delay_worker(sc: Scenario, guard: Guard, results: list, base_t: float) -> tuple[list, list[str]]:
    """Run the DELAY worker once, DELAY_MINUTES after the scenario's last request, and check
    each `after_hold` expectation. Uses a throwaway approval key: this is a local CLI run."""
    if not any(res is not None and res.verdict.verdict is Decision.DELAY for _, res, _ in results):
        return results, []
    later = base_t + max(r.t for r in sc.requests) + guard.settings.delay_minutes * 60 + 1
    key = GuardKey(Ed25519PrivateKey.generate())
    holds = HoldManager(guard, Approver(key, guard.settings.approval_ttl_seconds, clock=lambda: later),
                        MockSigner(key.public_key_hex, clock=lambda: later))
    outcome = {d["id"]: d for d in holds.release_due(now=later)}
    lines = [f"DELAY worker for {sc.name}, at +{guard.settings.delay_minutes} min after the last request:"]
    checked = []
    for r, res, status in results:
        d = outcome.get(res.check_id) if res is not None else None
        if d is not None:
            idx = sc.requests.index(r) + 1
            lines.append(f"  #{idx} {r.label}: {d['status'].upper()}, signed={d['signed']}")
            lines.append(f"       {d['reason']}")
        want = r.expect.after_hold
        if want is not None:
            got = d["status"] if d else "not held"
            if got != want and status == "OK":
                status = f"MISMATCH: after hold {got} != {want}"
        checked.append((r, res, status))
    return checked, lines


def cmd_run_all(directory: Path, db: str) -> int:
    base = get_settings()
    history = fresh_history(db)
    clock = {"now": T0}
    rows = []
    radius: list[str] = []
    worker: list[str] = []
    files = discover(directory)
    for n, path in enumerate(files):
        sc = load(path)
        guard = make_guard(sc, base, history, clock)
        results = run_file(sc, guard, clock, T0 + n * DAY, report=False)
        results, lines = run_delay_worker(sc, guard, results, T0 + n * DAY)
        worker += lines
        for r, res, status in results:
            rows.append((sc, r, res, status))
        radius += blast_radius(history, sc)

    ok = all(status == "OK" for *_, status in rows)
    mode = LLMExplainer(base).mode
    print(f"Fail Closed | run-all | {len(files)} scenarios, {len(rows)} requests | "
          f"LLM: {mode.upper()}{' (no model called)' if mode == 'mock' else ''}")
    head = f"{'scenario':<34} {'#':>2}  {'request':<44} {'path':<5} {'verdict':<11} {'rules':<16} {'ms':>6}  check"
    print(head)
    print("-" * len(head))
    last = None
    for sc, r, res, status in rows:
        name = sc.name if sc.name != last else ""
        idx = sc.requests.index(r) + 1
        if res is None:  # a human breaker action
            print(f"{name:<34} {idx:>2}  {('HUMAN: ' + r.label)[:44]:<44} {'-':<5} {r.action[:11]:<11} "
                  f"{'-':<16} {'':>6}  {status}")
            last = sc.name
            continue
        v = res.verdict
        rules = ",".join(dict.fromkeys(f.rule_id for f in v.findings if f.action is not Decision.ALLOW)) or "-"
        print(f"{name:<34} {idx:>2}  {r.label[:44]:<44} {v.path.value if v.path else '-':<5} "
              f"{v.verdict.value:<11} {rules:<16} {res.timings.get('total', 0) * 1000:6.0f}  {status}")
        for a in res.breaker_actions:
            print(f"{'':<38}  -> breaker {a}")
        last = sc.name
    print("")
    print_breaker(history)
    if radius:
        print("")
        print("\n".join(radius))
    if worker:
        print("")
        print("\n".join(worker))
    print("")
    print("all expectations met" if ok else "SOME EXPECTATIONS NOT MET")
    return 0 if ok else 1


def cmd_scenario(path: Path, db: str) -> int:
    sc = load(path)
    history = fresh_history(db)
    clock = {"now": T0}
    guard = make_guard(sc, get_settings(), history, clock)
    results = run_file(sc, guard, clock, T0, report=True)
    results, worker = run_delay_worker(sc, guard, results, T0)
    print("")
    print_breaker(history)
    if lines := blast_radius(history, sc):
        print("")
        print("\n".join(lines))
    if worker:
        print("")
        print("\n".join(worker))
    return 0 if all(status == "OK" for *_, status in results) else 1


# ---- reports -----------------------------------------------------------------------

def print_check(sc: Scenario, i: int, r: ScenarioRequest, res: CheckResult, guard: Guard,
                status: str) -> None:
    p = print
    tx, v, d, sim = r.tx(), res.verdict, res.decoded, res.simulation
    p("=" * 86)
    p(f" {sc.title}")
    p(f" request {i}/{len(sc.requests)}: {r.label}")
    p("=" * 86)
    p(f"request : {tx.wallet_type.value} on chain {tx.chain_id}, shape {d.kind.value if d else '?'}"
      + (f" ({d.note})" if d and d.note else ""))
    p(f"  from  : {lab(sc, tx.from_)}")
    p(f"  to    : {lab(sc, tx.to)}")
    p(f"  data  : {tx.data}   value: {eth(tx.value)}")
    for a in tx.authorization_list or []:
        p(f"  auth  : chain {a.chain_id}  delegate {lab(sc, a.address)}  nonce {a.nonce}")

    p("")
    p(f"path    : {v.path.value.upper() if v.path else '-'}")
    for reason in v.path_reasons:
        p(f"  - {reason}")

    if sim is not None:
        p("")
        if not sim.ok:
            p(f"--- sandbox FAILED -> R0: {sim.error} ---")
        else:
            mode = {"type4": "type4 = real EIP-7702 transaction",
                    "effect": "effect = delegation applied without signatures (no wallet keys)"}
            p(f"--- sandbox: state diff (prestateTracer, diffMode)"
              + (f"; delegation mode {mode.get(sim.delegation_mode, sim.delegation_mode)}" if sim.delegation_mode else "")
              + " ---")
            diff = sim.state_diff or {}
            pre_all, post_all = diff.get("pre", {}), diff.get("post", {})
            for addr in sorted(post_all):
                pre, post = pre_all.get(addr, {}), post_all[addr]
                p(f"{lab(sc, addr)}")
                if (c := field_change(pre, post, "code")) is not None:
                    p(f"    code    : {c[0] or '0x'} -> {c[1]}")
                    if str(c[1]).startswith("0xef0100"):
                        p(f"    >>> EIP-7702 delegation: the wallet now runs the code of {lab(sc, '0x' + str(c[1])[8:])}")
                if (n := field_change(pre, post, "nonce")) is not None:
                    p(f"    nonce   : {as_int(n[0])} -> {as_int(n[1])}")
                if (b := field_change(pre, post, "balance")) is not None:
                    before, after = as_int(b[0]), as_int(b[1])
                    sign = "+" if after >= before else "-"
                    p(f"    balance : {eth(before)} -> {eth(after)}  ({sign}{eth(abs(after - before))})")
                if post.get("storage"):
                    p(f"    storage : {len(post['storage'])} slot(s) changed")
            for addr in sorted(post_all):
                if str(post_all[addr].get("code", "")).startswith("0xef0100"):
                    p(f"raw diff {addr}:")
                    p(f"    pre : {json.dumps(pre_all.get(addr, {}))}")
                    p(f"    post: {json.dumps(post_all[addr])}")
            auths = (sim.tx or {}).get("authorizationList") or []
            if auths:
                p("authorizationList (" + ("mined type-4 tx" if sim.delegation_mode == "type4"
                                           else "from the request, effect simulated") + "):")
                who = tx.authorization_list[0].authority or tx.from_
                start = as_int(pre_all.get(who, {}).get("nonce", 0)) + (1 if who == tx.from_ else 0)
                for k, (a, o) in enumerate(zip(auths, auth_outcomes(auths, start, tx.chain_id))):
                    p(f"  #{k}  chainId {as_int(a['chainId'])}  address {a['address']}  nonce {as_int(a['nonce'])}  -> {o}")
            logs = (sim.receipt or {}).get("logs") or []
            p(f"receipt status {(sim.receipt or {}).get('status')}, {len(logs)} log(s)")
            if sim.probe:
                p(f"probe after delegation: {len(sim.probe)} call(s) from outside accounts")
                for k, pr in enumerate(sim.probe, 1):
                    moved = []
                    for to, amt in (pr.get("native_out") or {}).items():
                        moved.append(f"{eth(int(amt))} to {lab(sc, to)}")
                    for t in pr.get("token_out") or []:
                        moved.append(f"{int(t['amount'])} units of {t['token']} to {t['to']}")
                    what = "MOVED " + ", ".join(moved) if moved else "nothing moved"
                    p(f"  #{k} {pr['caller']} ({pr['caller_kind']}) calls {pr['call']:<11} "
                      f"-> status {pr['status']}, {what}")

    p("")
    p("--- findings ---")
    if not v.findings:
        p("  (none)")
    for f in v.findings:
        p(f"  [{f.action.value}] {f.rule_id}: {f.title}")
        if f.detail:
            p(f"           {f.detail}")
    p("")
    p(f"VERDICT : {v.verdict.value}   (expected {r.expect.verdict}: {status})")
    if v.release_at:
        p(f"          held until t+{v.release_at - T0:.0f}s, then released if no other signal")
    for a in res.breaker_actions:
        p(f"breaker : {a}")

    p("")
    llm = res.llm
    waited = None
    if res.explanation is not None:  # made in the background; the verdict did not wait for it
        t0 = time.perf_counter()
        llm = res.explanation.result(timeout=60)
        waited = (time.perf_counter() - t0) * 1000
    if waited is not None:
        p(f"(explanation made asynchronously, after the verdict; it arrived {waited:.0f} ms later)")
    if llm is None:
        p(f"--- LLM ({guard.explainer.mode}) --- no explanation (not called on this path, failed, or off); "
          "the verdict is the same either way")
    else:
        tag = "MOCK - no model was called" if llm.mock else f"Vultr Inference, model {guard.settings.vultr_model}"
        p(f"--- LLM ({tag}) ---")
        p(f"  intent_summary : {llm.intent_summary}")
        p(f"  risk_score     : {llm.risk_score}/100   escalate: {llm.escalate}")
        p(f"  explanation    : {llm.explanation}")

    p("")
    p("--- timings ---")
    names = [("state", "starting state (StateProvider)"), ("request_rules", "request/history rules"),
             ("sandbox.container_start", "sandbox: container start + anvil ready"),
             ("sandbox.state_inject", "sandbox: state inject + read-back"),
             ("sandbox.tx", "sandbox: tx (delegation + send)"), ("sandbox.trace", "sandbox: receipt + trace"),
             ("sandbox.probe", "sandbox: probe"), ("sandbox.destroy", "sandbox: container destroy"),
             ("llm", "LLM"), ("decide", "decide"), ("total", "total")]
    for key, name in names:
        if key in res.timings:
            p(f"  {name:<40} {res.timings[key] * 1000:8.0f} ms")

    if sim is not None and sim.sandbox:
        lim = sim.sandbox
        p("")
        p("--- sandbox container limits (docker inspect, as applied) ---")
        p(f"  NetworkMode {lim.get('NetworkMode')} | ReadonlyRootfs {lim.get('ReadonlyRootfs')} | "
          f"Memory {(lim.get('Memory') or 0) // 2**20} MiB (swap {(lim.get('MemorySwap') or 0) // 2**20} MiB) | "
          f"{(lim.get('NanoCpus') or 0) / 1e9:g} CPU | PidsLimit {lim.get('PidsLimit')}")
        p(f"  CapDrop {lim.get('CapDrop')} | CapAdd {lim.get('CapAdd')} | SecurityOpt {lim.get('SecurityOpt')} | "
          f"Privileged {lim.get('Privileged')} | Binds {lim.get('Binds')} | User {lim.get('User')}")
        p(f"  Tmpfs {sorted((lim.get('Tmpfs') or {}).keys())} | Env {lim.get('Env')} (image default; the Guard passes none)")
    p("")


def print_breaker(history: History) -> None:
    frozen, paused = history.frozen(), history.paused()
    safe, stop = history.safe_modes(), history.global_stop()
    print("--- circuit breaker (latched until a human resets) ---")
    print(f"frozen destinations (level 1): {len(frozen)}")
    for r in frozen:
        print(f"  {r['address']}  {r['reason']}")
    print(f"paused wallets (level 2): {len(paused)}")
    for r in paused:
        print(f"  {r['wallet']}  {r['reason']}")
    print(f"safe mode chains (level 3): {len(safe)}")
    for r in safe:
        print(f"  chain {r['chain_id']}  {r['reason']}")
    print(f"global stop (level 4): {'ON - ' + stop['reason'] if stop else 'off'}")


def print_redteam_row(r: dict) -> None:
    print(f"{r['status'].upper():<10} {r['name']:<12} {r['seconds']:>5.1f}s  {r['limit']}")
    print(f"{'':<10} tried : {r['tried']}")
    print(f"{'':<10} result: {r['result']}")
    if r.get("health"):
        h = r["health"]
        print(f"{'':<10} host health during the run: {h['answered']}/{h['calls']} answered, "
              f"avg {h['avg_ms']} ms, max {h['max_ms']} ms")


def cmd_redteam(health_url: str | None) -> int:
    from app.api import http_health
    from redteam import RedTeam

    probe = (lambda: http_health(health_url)) if health_url else None
    print("Fail Closed | red-team containment | same limits as a normal check")
    report = RedTeam(get_settings(), health_probe=probe).run(on_result=print_redteam_row)
    print("")
    print("ALL CONTAINED" if report["all_contained"] else f"RED: {', '.join(report['red'])}")
    return 0 if report["all_contained"] else 1


def cmd_loadtest(args) -> int:
    from loadtest import CONFIGS, markdown_table, machine_info, run_all

    import threading

    import loadtest_safety as safety

    free = safety.free_ram_gb()
    free_txt = "unknown" if free is None else f"{free:.1f} GB"
    if free is None or free < args.min_free_gb:
        print(f"refusing to start: free RAM {free_txt}, need at least {args.min_free_gb:g} GB "
              "(close other programs, or lower --min-free-gb at your own risk)")
        return 2
    owner = safety.owner_for()
    base = Settings(**{**get_settings().model_dump(), "mock_llm_latency_ms": args.latency_ms,
                       "mock_llm_escalate_rate": args.escalate_rate, "sandbox_owner": owner,
                       **({"ai_check_concurrency": args.ai_concurrency} if args.ai_concurrency else {})})
    configs = list(CONFIGS) if args.configs == "all" else args.configs.split(",")
    live = args.llm == "live"
    if live and not base.vultr_inference_api_key:
        print("--llm live needs VULTR_INFERENCE_API_KEY (key not set)")
        return 2
    try:
        import docker

        client_factory = docker.from_env
        stale = safety.remove_containers(client_factory(), stale=True)
    except Exception as e:  # noqa: BLE001 - the run itself will report Docker problems
        client_factory, stale = None, 0
        print(f"note: could not check for leftover containers ({type(e).__name__})")
    if client_factory is not None:
        safety.Cleanup(owner, client_factory).install()
    llm = (f"REAL LLM {base.vultr_model}, AI concurrency {base.ai_check_concurrency}" if live
           else f"MOCK LLM {args.latency_ms} ms, escalates {args.escalate_rate:.0%}")
    print(f"Fail Closed | load test {args.normal} normal + 1 attack | configs: {', '.join(configs)} | {llm}")
    print(f"free RAM {free_txt}; containers labelled fail-closed.owner={owner}"
          + (f"; removed {stale} left over by an earlier run" if stale else ""))
    out: dict = {}

    def work() -> None:
        try:
            out["results"] = run_all(base, Path(args.out), args.normal, args.seed, args.concurrency, configs,
                                     llm_live=live, new_dest_share=args.new_dest_share)
        except BaseException as e:  # noqa: BLE001 - reported below
            out["error"] = e

    # The run is in a worker thread; the main thread only waits in short steps, so Ctrl+C and
    # SIGTERM are handled at once (cleanup, then exit) even while every worker is blocked.
    t = threading.Thread(target=work, name="loadtest-main", daemon=True)
    t.start()
    while t.is_alive():
        t.join(0.5)
    if "error" in out:
        raise out["error"]
    results = out["results"]
    print("")
    print(markdown_table(results, machine_info()))
    print(f"saved to {args.out}/")
    return 0 if all(r["attacks_stopped"] == r["attacks"] for r in results) else 1


def cmd_reset(db: str, address: str | None, wallet: str | None, actor: str,
              chain: int | None = None, global_stop: bool = False) -> int:
    h = History(db)
    b = Breaker(h)
    now = time.time()
    if address:
        ok = b.reset_destination(address, actor=actor, now=now)
        print(f"{'reset' if ok else 'not frozen'}: destination {address.lower()} (by {actor}, logged)")
    elif wallet:
        ok = b.reset_wallet(wallet, actor=actor, now=now)
        print(f"{'reset' if ok else 'not paused'}: wallet {wallet.lower()} (by {actor}, logged)")
    elif chain is not None:
        ok = b.reset_safe_mode(chain, actor=actor, now=now)
        print(f"{'reset' if ok else 'not in safe mode'}: chain {chain} (by {actor}, logged)")
    else:
        ok = b.reset_global(actor=actor, now=now)
        print(f"{'reset' if ok else 'not active'}: global stop (by {actor}, logged)")
    print_breaker(h)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    default_db = str(REPO / "data" / "run.sqlite3")
    ap = argparse.ArgumentParser(prog="fail-closed")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ra = sub.add_parser("run-all", help="run every scenario in order; one table + breaker state")
    ra.add_argument("--dir", type=Path, default=REPO / "scenarios")
    ra.add_argument("--db", default=default_db, help="history db, recreated on each run")
    sc = sub.add_parser("scenario", help="run one scenario file with the full report")
    sc.add_argument("path", type=Path)
    sc.add_argument("--db", default=default_db, help="history db, recreated on each run")
    rs = sub.add_parser("reset", help="human reset of a latched freeze or pause (logged)")
    target = rs.add_mutually_exclusive_group(required=True)
    target.add_argument("--address", help="unfreeze a destination address (level 1)")
    target.add_argument("--wallet", help="unpause a wallet (level 2)")
    target.add_argument("--chain", type=int, help="end safe mode on a chain (level 3)")
    target.add_argument("--global", dest="global_stop", action="store_true", help="lift the global stop (level 4)")
    rs.add_argument("--actor", default="cli", help="who resets (written to the log)")
    rs.add_argument("--db", default=None, help="history database (default: HISTORY_DB)")
    br = sub.add_parser("breaker", help="show frozen destinations and paused wallets")
    br.add_argument("--db", default=None)
    rt = sub.add_parser("redteam", help="run hostile payloads in the real sandbox; RED if any escapes")
    rt.add_argument("--health-url", default=None, help="API /health to call during the fork/memory bombs")
    lt = sub.add_parser("loadtest", help="500 normal + 1 attack through the real Guard, 4 configs")
    lt.add_argument("--normal", type=int, default=500)
    lt.add_argument("--seed", type=int, default=7)
    lt.add_argument("--concurrency", type=int, default=8)
    lt.add_argument("--configs", default="all", help="all, or e.g. fresh/escalation-on,warm/escalation-off")
    lt.add_argument("--latency-ms", type=int, default=800, help="mock LLM latency per call")
    lt.add_argument("--escalate-rate", type=float, default=0.05, help="share of plain transfers the mock escalates")
    lt.add_argument("--min-free-gb", type=float, default=2.0, help="refuse to start with less free RAM")
    lt.add_argument("--ai-concurrency", type=int, default=0, help="fast-path AI calls at once (0 = settings)")
    lt.add_argument("--new-dest-share", type=float, default=0.1,
                    help="share of normal requests to a destination the wallet never paid before")
    lt.add_argument("--llm", choices=["mock", "live"], default="mock",
                    help="mock (default) or live: the real Vultr model from .env (costs money)")
    lt.add_argument("--out", default=str(REPO / "docs" / "results"))
    args = ap.parse_args(argv)
    if args.cmd == "loadtest":
        return cmd_loadtest(args)
    if args.cmd == "redteam":
        return cmd_redteam(args.health_url)

    if args.cmd == "run-all":
        return cmd_run_all(args.dir, args.db)
    if args.cmd == "scenario":
        return cmd_scenario(args.path, args.db)
    if args.cmd == "reset":
        return cmd_reset(args.db or get_settings().history_db, args.address, args.wallet, args.actor,
                         args.chain, args.global_stop)
    if args.cmd == "breaker":
        print_breaker(History(args.db or get_settings().history_db))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
