"""Simulation rules. They run on the slow path only, on a usable simulation (R0 is in decide())."""

from app.models import Decision, Finding, WalletType
from decode import authority_of
from policies.engine import RuleContext, RuleInputMissing
from policies.fmt import amount, over_pct, pct, short

DELEGATION_PREFIX = "0xef0100"
ZERO = "0x" + "00" * 20
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def _resolved(pre: dict, post: dict, field: str, empty):
    """diffMode: pre holds the full prior account, post only changed fields."""
    before = pre.get(field, empty)
    after = post.get(field, before)
    return before, after


def _int(v) -> int:
    return int(v, 16) if isinstance(v, str) else int(v or 0)


def r1_delegation(ctx: RuleContext) -> list[Finding]:
    ctx.need("simulation", "settings")
    diff = ctx.simulation.state_diff or {}
    pre_all, post_all = diff.get("pre", {}), diff.get("post", {})
    findings = []
    for addr, post in post_all.items():
        code = str(post.get("code", ""))
        if not code.startswith(DELEGATION_PREFIX):
            continue
        delegate = "0x" + code[len(DELEGATION_PREFIX):]
        if delegate == ZERO or delegate in ctx.settings.delegate_allowlist:
            continue
        before = pre_all.get(addr, {}).get("code", "0x")
        mode = ctx.simulation.delegation_mode or "unknown"
        findings.append(Finding(
            rule_id="R1", action=Decision.BLOCK, subject=addr,
            title="Wallet would be delegated to an unknown contract (EIP-7702)",
            detail=f"{addr} gets code {code} (was {before}): from now on it runs the code of "
                   f"{delegate}, which is not on the delegate allowlist. Whoever controls that "
                   f"contract controls the wallet. (simulation: {mode})"))
    return findings


def _guarded(ctx: RuleContext) -> list[str]:
    out = [ctx.tx.from_]
    for a in ctx.tx.authorization_list or []:
        w = authority_of(a, ctx.tx)
        if w not in out:
            out.append(w)
    return out


def _token_transfers(logs: list, wallet: str, known: list[str]) -> list[tuple[str, str, int]]:
    out = []
    for log in logs or []:
        topics = [t.lower() for t in log.get("topics", [])]
        token = str(log.get("address", "")).lower()
        if len(topics) == 3 and topics[0] == TRANSFER_TOPIC and token in known \
                and "0x" + topics[1][-40:] == wallet:
            out.append((token, "0x" + topics[2][-40:], _int(log.get("data", "0x0") or "0x0")))
    return out


def r3_simulation_outflow(ctx: RuleContext) -> list[Finding]:
    """Wallet loses more than R3 % of a balance to one non-allowlisted address.

    Evidence: native from the state diff, tokens from Transfer logs of KNOWN_TOKENS only,
    plus the post-delegation probe calls.
    """
    ctx.need("simulation", "settings", "state")
    s, sim = ctx.settings, ctx.simulation
    allow = set(s.treasury_allowlist) if ctx.tx.wallet_type is WalletType.TREASURY else set()
    limit = s.r3_max_single_dest_pct
    diff = sim.state_diff or {}
    pre_all, post_all = diff.get("pre", {}), diff.get("post", {})
    findings: list[Finding] = []
    seen: set[tuple] = set()

    def balance_of(wallet: str, asset: str) -> int:
        acct = ctx.state.accounts.get(wallet)
        if asset == "native":
            return acct.balance if acct else 0
        if acct is None or asset not in acct.token_balances:
            raise RuleInputMissing(f"token balance of {wallet} for {asset}")
        return acct.token_balances[asset]

    def check(wallet: str, asset: str, to: str, value: int, evidence: str) -> None:
        if value <= 0 or to in allow or to == wallet:
            return
        base = balance_of(wallet, asset)
        if base == 0 or over_pct(value, base, limit):
            key = (wallet, asset, to)
            if key in seen:
                return
            seen.add(key)
            findings.append(Finding(
                rule_id="R3", action=Decision.BLOCK, subject=to,
                title=f"Wallet loses more than {limit:g}% of its balance to one address",
                detail=f"{wallet} -> {to}: {amount(asset, value)} of {amount(asset, base)} "
                       f"({pct(value, base)}). Evidence: {evidence}."))

    for wallet in _guarded(ctx):
        # native, from the diff of the request itself
        w_before, w_after = _resolved(pre_all.get(wallet, {}), post_all.get(wallet, {}), "balance", "0x0")
        loss = _int(w_before) - _int(w_after)
        if loss > 0:
            for addr, post in post_all.items():
                if addr == wallet:
                    continue
                b, a = _resolved(pre_all.get(addr, {}), post, "balance", "0x0")
                gain = _int(a) - _int(b)
                if gain > 0:
                    check(wallet, "native", addr, min(gain, loss), "state diff of the request")
        # tokens, from Transfer logs of known tokens
        for token, to, value in _token_transfers((sim.receipt or {}).get("logs"), wallet, s.known_tokens):
            check(wallet, token, to, value, "Transfer log of the request")
        # post-delegation probe
        for p in sim.probe:
            if p.get("wallet") != wallet:
                continue
            ev = f"probe: {short(p['caller'])} ({p['caller_kind']}) called {p['call']} on the delegated wallet"
            for to, value in (p.get("native_out") or {}).items():
                check(wallet, "native", to, int(value), ev)
            for t in p.get("token_out") or []:
                if t["token"] in s.known_tokens:
                    check(wallet, t["token"], t["to"], int(t["amount"]), ev)
    return findings


SIM_RULES = [r1_delegation, r3_simulation_outflow]
