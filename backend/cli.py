"""Run a scenario end to end and print what the sandbox saw.

    python backend/cli.py scenario scenarios/7702_attack [--json]

Prints: the diff lines (incl. 0xef0100 delegation code), balance changes, the mined
authorizationList, the LLM result (clearly marked MOCK when it is the mock), the time of
each step, and the container limits as applied by Docker (docker inspect).
Rules are not built yet (Phase 2), so no verdict is computed here.
"""

import argparse
import json
import logging
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.config import Settings, get_settings
from app.models import SimulationResult, TxRequest
from llm import LLMExplainer
from sandbox_runner import SandboxError, SandboxRunner
from state_provider import ScenarioStateProvider

ZERO = {"balance": "0x0", "nonce": 0, "code": "0x"}


def eth(wei: int) -> str:
    return f"{Decimal(wei) / Decimal(10**18):,.6f} ETH"


def field_change(pre: dict, post: dict, field: str) -> tuple[Any, Any] | None:
    """In diffMode a field listed on only one side means the other side is zero/empty."""
    if field not in pre and field not in post:
        return None
    return pre.get(field, ZERO[field]), post.get(field, ZERO[field])


def as_int(v: Any) -> int:
    return int(v, 16) if isinstance(v, str) else int(v)


def label(addr: str, labels: dict[str, str]) -> str:
    name = labels.get(addr.lower())
    return f"{addr} ({name})" if name else addr


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


def print_report(tx: TxRequest, sim: SimulationResult | None, err: str | None, llm, llm_time: float,
                 llm_mode: str, settings: Settings, labels: dict[str, str], title: str) -> None:
    p = print
    p("=" * 78)
    p(f" Fail Closed | scenario: {title}")
    p("=" * 78)
    p(f"request : {tx.wallet_type.value} on chain {tx.chain_id}")
    p(f"  from  : {label(tx.from_, labels)}")
    p(f"  to    : {label(tx.to, labels)}")
    p(f"  data  : {tx.data}   value: {eth(tx.value)}")
    p(f"  auths : {len(tx.authorization_list or [])} EIP-7702 authorization(s) in the request")

    if sim is None:
        p("")
        p(f"SANDBOX FAILED -> R0 BLOCK: {err}")
    else:
        diff = sim.state_diff or {}
        pre_all, post_all = diff.get("pre", {}), diff.get("post", {})

        p("")
        p("--- state diff (debug_traceTransaction, prestateTracer, diffMode) ---")
        for addr in sorted(set(pre_all) | set(post_all)):
            pre, post = pre_all.get(addr, {}), post_all.get(addr, {})
            p(f"{label(addr, labels)}")
            if (c := field_change(pre, post, "code")) is not None:
                before, after = c
                p(f"    code    : {before or '0x'} -> {after}")
                if str(after).startswith("0xef0100"):
                    delegate = "0x" + str(after)[8:]
                    p(f"    >>> EIP-7702 delegation: account now runs the code of {label(delegate, labels)}")
            if (n := field_change(pre, post, "nonce")) is not None:
                p(f"    nonce   : {as_int(n[0])} -> {as_int(n[1])}")
            if (b := field_change(pre, post, "balance")) is not None:
                before, after = as_int(b[0]), as_int(b[1])
                p(f"    balance : {eth(before)} -> {eth(after)}  ({'+' if after >= before else '-'}{eth(abs(after - before))})")
            if pre.get("storage") or post.get("storage"):
                p(f"    storage : {len(post.get('storage', {}))} slot(s) changed")
        hot = tx.to.lower()
        if hot in post_all:
            p("")
            p(f"raw diff for {tx.to}:")
            p(f"    pre : {json.dumps(pre_all.get(hot, {}))}")
            p(f"    post: {json.dumps(post_all.get(hot, {}))}")

        p("")
        p("--- authorizationList (from the mined type-4 tx) ---")
        auths = (sim.tx or {}).get("authorizationList") or []
        authority = (tx.authorization_list[0].authority if tx.authorization_list else None) or ""
        start_nonce = as_int(pre_all.get(authority, {}).get("nonce", 0)) if authority else 0
        for i, (a, outcome) in enumerate(zip(auths, auth_outcomes(auths, start_nonce, tx.chain_id))):
            p(f"  #{i}  chainId {as_int(a['chainId'])}  address {a['address']}  nonce {as_int(a['nonce'])}"
              f"  yParity {as_int(a.get('yParity', '0x0'))}  -> {outcome}")
        if not auths:
            p("  (none)")
        p(f"receipt status: {(sim.receipt or {}).get('status')}  (0x1 = success)")

    p("")
    if llm is None:
        p(f"--- LLM ({llm_mode}, model {settings.vultr_model}) ---")
        p("  no explanation (LLM failed or off). Verdict would come from the rules only.")
    else:
        tag = "MOCK - no model was called" if llm.mock else f"Vultr Inference, model {settings.vultr_model}"
        p(f"--- LLM ({tag}) ---")
        p(f"  intent_summary : {llm.intent_summary}")
        p(f"  risk_score     : {llm.risk_score}/100")
        p(f"  escalate       : {llm.escalate}")
        p(f"  explanation    : {llm.explanation}")

    p("")
    p("--- timings ---")
    timings = dict(sim.timings) if sim else {}
    timings["llm"] = llm_time
    order = [("container_start", "container start (+ anvil ready)"), ("state_inject", "state inject (+ read-back)"),
             ("tx", "tx (sign auths + send)"), ("trace", "receipt + trace + tx"), ("llm", "LLM"),
             ("destroy", "container destroy")]
    for key, name in order:
        if key in timings:
            p(f"  {name:<34} {timings[key]*1000:8.0f} ms")
    p(f"  {'total':<34} {sum(timings.values())*1000:8.0f} ms")

    if sim is not None:
        p("")
        p("--- sandbox container limits (docker inspect, as applied) ---")
        lim = sim.sandbox
        mem = lim.get("Memory") or 0
        p(f"  NetworkMode    : {lim.get('NetworkMode')}")
        p(f"  ReadonlyRootfs : {lim.get('ReadonlyRootfs')}")
        p(f"  Memory         : {mem} bytes ({mem // 2**20} MiB), MemorySwap {lim.get('MemorySwap')}")
        p(f"  NanoCpus       : {lim.get('NanoCpus')} ({(lim.get('NanoCpus') or 0) / 1e9:g} CPU)")
        p(f"  PidsLimit      : {lim.get('PidsLimit')}")
        p(f"  CapDrop        : {lim.get('CapDrop')}   CapAdd: {lim.get('CapAdd')}")
        p(f"  SecurityOpt    : {lim.get('SecurityOpt')}")
        p(f"  Privileged     : {lim.get('Privileged')}")
        p(f"  Tmpfs          : {lim.get('Tmpfs')}")
        p(f"  Binds          : {lim.get('Binds')}")
        p(f"  User           : {lim.get('User')}")
        p(f"  Env            : {lim.get('Env')}  (image defaults; the Guard passes none)")

    p("")
    p("verdict : not computed - rules R0-R8 come in Phase 2.")


def run_scenario(path: Path, as_json: bool) -> int:
    settings = get_settings()
    meta_file = path / "scenario.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
    labels = {k.lower(): v for k, v in meta.get("labels", {}).items()}

    try:
        tx = TxRequest.model_validate(json.loads((path / "request.json").read_text(encoding="utf-8")))
    except (ValidationError, ValueError) as e:
        print(f"invalid request.json (would be HTTP 422): {e}", file=sys.stderr)
        return 2
    state = ScenarioStateProvider(path / "state.json").get_state(tx)

    sim, err = None, None
    try:
        sim = SandboxRunner(settings).run(tx, state)
    except SandboxError as e:
        err = str(e)

    explainer = LLMExplainer(settings)
    t0 = time.perf_counter()
    llm = explainer.assess(tx, sim if sim else SimulationResult(ok=False, error=err))
    llm_time = time.perf_counter() - t0

    if as_json:
        print(json.dumps({
            "request": tx.model_dump(mode="json", by_alias=True),
            "simulation": sim.model_dump(mode="json") if sim else None,
            "sandbox_error": err,
            "llm": llm.model_dump(mode="json") if llm else None,
            "llm_seconds": llm_time,
        }, indent=2))
    else:
        print_report(tx, sim, err, llm, llm_time, explainer.mode, settings, labels, meta.get("title", path.name))
    return 0 if sim is not None else 1


def print_breaker(history) -> None:
    frozen, paused = history.frozen(), history.paused()
    print("--- circuit breaker (latched until a human resets) ---")
    print(f"frozen destinations (level 1): {len(frozen)}")
    for r in frozen:
        print(f"  {r['address']}  {r['reason']}")
    print(f"paused wallets (level 2): {len(paused)}")
    for r in paused:
        print(f"  {r['wallet']}  {r['reason']}")


def cmd_reset(db: str, address: str | None, wallet: str | None, actor: str) -> int:
    from breaker import Breaker
    from history import History

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
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="fail-closed")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("scenario", help="run a scenario directory end to end")
    sc.add_argument("path", type=Path)
    sc.add_argument("--json", action="store_true", help="print raw JSON instead of the report")
    rs = sub.add_parser("reset", help="human reset of a latched freeze or pause (logged)")
    target = rs.add_mutually_exclusive_group(required=True)
    target.add_argument("--address", help="unfreeze a destination address (level 1)")
    target.add_argument("--wallet", help="unpause a wallet (level 2)")
    rs.add_argument("--actor", default="cli", help="who resets (written to the log)")
    rs.add_argument("--db", default=None, help="history database (default: HISTORY_DB)")
    br = sub.add_parser("breaker", help="show frozen destinations and paused wallets")
    br.add_argument("--db", default=None)
    args = ap.parse_args(argv)
    if args.cmd == "scenario":
        return run_scenario(args.path, args.json)
    if args.cmd == "reset":
        return cmd_reset(args.db or get_settings().history_db, args.address, args.wallet, args.actor)
    if args.cmd == "breaker":
        from history import History

        print_breaker(History(args.db or get_settings().history_db))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
