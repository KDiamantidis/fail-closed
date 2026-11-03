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

from app.config import Settings, get_settings
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


def expect_status(r: ScenarioRequest, res: CheckResult) -> str:
    v = res.verdict
    problems = []
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
                 breaker=Breaker(history), clock=lambda: clock["now"])


def fresh_history(db: str) -> History:
    p = Path(db)
    if p.exists():
        p.unlink()
    return History(p)


def run_file(sc: Scenario, guard: Guard, clock: dict, base_t: float, report: bool):
    results = []
    for i, r in enumerate(sc.requests, 1):
        clock["now"] = base_t + r.t
        res = guard.check(r.tx())
        results.append((r, res))
        if report:
            print_check(sc, i, r, res, guard)
    return results


def cmd_run_all(directory: Path, db: str) -> int:
    base = get_settings()
    history = fresh_history(db)
    clock = {"now": T0}
    rows = []
    files = discover(directory)
    for n, path in enumerate(files):
        sc = load(path)
        guard = make_guard(sc, base, history, clock)
        for r, res in run_file(sc, guard, clock, T0 + n * DAY, report=False):
            rows.append((sc, r, res))

    ok = all(expect_status(r, res) == "OK" for _, r, res in rows)
    mode = LLMExplainer(base).mode
    print(f"Fail Closed | run-all | {len(files)} scenarios, {len(rows)} requests | "
          f"LLM: {mode.upper()}{' (no model called)' if mode == 'mock' else ''}")
    head = f"{'scenario':<34} {'#':>2}  {'request':<44} {'path':<5} {'verdict':<11} {'rules':<16} {'ms':>6}  check"
    print(head)
    print("-" * len(head))
    last = None
    for sc, r, res in rows:
        v = res.verdict
        rules = ",".join(dict.fromkeys(f.rule_id for f in v.findings if f.action is not Decision.ALLOW)) or "-"
        name = sc.name if sc.name != last else ""
        idx = sc.requests.index(r) + 1
        print(f"{name:<34} {idx:>2}  {r.label[:44]:<44} {v.path.value if v.path else '-':<5} "
              f"{v.verdict.value:<11} {rules:<16} {res.timings.get('total', 0) * 1000:6.0f}  {expect_status(r, res)}")
        for a in res.breaker_actions:
            print(f"{'':<38}  -> breaker {a}")
        last = sc.name
    print("")
    print_breaker(history)
    print("")
    print("all expectations met" if ok else "SOME EXPECTATIONS NOT MET")
    return 0 if ok else 1


def cmd_scenario(path: Path, db: str) -> int:
    sc = load(path)
    history = fresh_history(db)
    clock = {"now": T0}
    guard = make_guard(sc, get_settings(), history, clock)
    results = run_file(sc, guard, clock, T0, report=True)
    print("")
    print_breaker(history)
    return 0 if all(expect_status(r, res) == "OK" for r, res in results) else 1


# ---- reports -----------------------------------------------------------------------

def print_check(sc: Scenario, i: int, r: ScenarioRequest, res: CheckResult, guard: Guard) -> None:
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
    p(f"VERDICT : {v.verdict.value}   (expected {r.expect.verdict}: {expect_status(r, res)})")
    if v.release_at:
        p(f"          held until t+{v.release_at - T0:.0f}s, then released if no other signal")
    for a in res.breaker_actions:
        p(f"breaker : {a}")

    p("")
    llm = res.llm
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
    print("--- circuit breaker (latched until a human resets) ---")
    print(f"frozen destinations (level 1): {len(frozen)}")
    for r in frozen:
        print(f"  {r['address']}  {r['reason']}")
    print(f"paused wallets (level 2): {len(paused)}")
    for r in paused:
        print(f"  {r['wallet']}  {r['reason']}")


def cmd_reset(db: str, address: str | None, wallet: str | None, actor: str) -> int:
    h = History(db)
    b = Breaker(h)
    now = time.time()
    if address:
        ok = b.reset_destination(address, actor=actor, now=now)
        print(f"{'reset' if ok else 'not frozen'}: destination {address.lower()} (by {actor}, logged)")
    else:
        ok = b.reset_wallet(wallet, actor=actor, now=now)
        print(f"{'reset' if ok else 'not paused'}: wallet {wallet.lower()} (by {actor}, logged)")
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
    rs.add_argument("--actor", default="cli", help="who resets (written to the log)")
    rs.add_argument("--db", default=None, help="history database (default: HISTORY_DB)")
    br = sub.add_parser("breaker", help="show frozen destinations and paused wallets")
    br.add_argument("--db", default=None)
    args = ap.parse_args(argv)

    if args.cmd == "run-all":
        return cmd_run_all(args.dir, args.db)
    if args.cmd == "scenario":
        return cmd_scenario(args.path, args.db)
    if args.cmd == "reset":
        return cmd_reset(args.db or get_settings().history_db, args.address, args.wallet, args.actor)
    if args.cmd == "breaker":
        print_breaker(History(args.db or get_settings().history_db))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
