"""Request / history rules. They run on EVERY request (fast and slow path).

Pure functions of the context: they read history, they never write it. Breaker effects
(freeze, pause) are applied by the guard after the verdict.
"""

from app.models import Decision, Effect, Finding, WalletType
from decode import Kind, authority_of
from policies.engine import RuleContext, RuleInputMissing
from policies.fmt import amount, over_pct, pct

HOUR = 3600.0
MINUTE = 60.0


def _wallets(ctx: RuleContext) -> list[str]:
    """The wallet(s) whose signature is requested: the sender and every authority."""
    out = [ctx.tx.from_]
    for a in ctx.tx.authorization_list or []:
        w = authority_of(a, ctx.tx)
        if w not in out:
            out.append(w)
    return out


def f1_frozen_destination(ctx: RuleContext) -> list[Finding]:
    ctx.need("history", "decoded")
    findings = []
    for addr in dict.fromkeys([ctx.decoded.destination, ctx.tx.to]):
        if (row := ctx.history.is_frozen(addr)) is not None:
            findings.append(Finding(
                rule_id="F1", action=Decision.BLOCK, subject=addr,
                title="Destination is frozen",
                detail=f"{addr} was frozen ({row['reason']}). It stays frozen until a human resets it."))
    return findings


def f2_paused_wallet(ctx: RuleContext) -> list[Finding]:
    ctx.need("history")
    findings = []
    for w in _wallets(ctx):
        if (row := ctx.history.is_paused(w)) is not None:
            findings.append(Finding(
                rule_id="F2", action=Decision.BLOCK, subject=w,
                title="Wallet is paused",
                detail=f"{w} was paused ({row['reason']}). It stays paused until a human resets it."))
    return findings


def f3_safe_mode_chain(ctx: RuleContext) -> list[Finding]:
    """Breaker level 3: the chain is in safe mode. Every request there needs a human (and is
    still checked in the sandbox: a finding means no fast path)."""
    ctx.need("history")
    row = ctx.history.is_safe_mode(ctx.tx.chain_id)
    if row is None:
        return []
    return [Finding(rule_id="F3", action=Decision.NEEDS_HUMAN, subject=str(ctx.tx.chain_id),
                    title=f"Chain {ctx.tx.chain_id} is in safe mode",
                    detail=f"{row['reason']}. No fast path on this chain; every request needs a human "
                           "until safe mode is reset.")]


def f4_global_stop(ctx: RuleContext) -> list[Finding]:
    """Breaker level 4: no approvals at all until a human resets it."""
    ctx.need("history")
    row = ctx.history.global_stop()
    if row is None:
        return []
    return [Finding(rule_id="F4", action=Decision.BLOCK, title="Global stop",
                    detail=f"{row['reason']}. No approvals at all until a human resets the global stop.")]


def r2_authorizations(ctx: RuleContext) -> list[Finding]:
    auths = ctx.tx.authorization_list or []
    if not auths:
        return []
    ctx.need("history", "settings")
    findings = []
    if len(auths) > 1:
        nonces = ", ".join(str(a.nonce) for a in auths)
        findings.append(Finding(
            rule_id="R2", action=Decision.BLOCK, subject=authority_of(auths[0], ctx.tx),
            title=f"{len(auths)} EIP-7702 authorizations in one request",
            detail=f"Nonces {nonces}. A wallet never needs more than one; many nonces at once is "
                   "how an attacker makes sure one of them matches."))
    since = ctx.now - ctx.settings.r2_window_hours * HOUR
    for a in auths:
        who = authority_of(a, ctx.tx)
        for chain, prev in ctx.history.recent_auth_nonces(who, since, exclude=ctx.exclude_check):
            same_chain = a.chain_id == 0 or chain == 0 or chain == a.chain_id
            if same_chain and abs(a.nonce - prev) == 1:
                findings.append(Finding(
                    rule_id="R2", action=Decision.BLOCK, subject=who,
                    title="Authorization nonce follows a recent one",
                    detail=f"{who} was asked to sign nonce {prev} recently and now nonce {a.nonce}: "
                           "consecutive authorizations from one authority."))
                break
    return findings


def r3_request_outflow(ctx: RuleContext) -> list[Finding]:
    """R3 before simulation, from the decoded transfer and the Guard-side starting state.
    No sandbox evidence -> NEEDS_HUMAN, never BLOCK, and never pauses the wallet."""
    ctx.need("decoded", "settings", "state")
    d = ctx.decoded
    if d.kind not in (Kind.NATIVE_TRANSFER, Kind.ERC20_TRANSFER) or d.amount == 0:
        return []
    if ctx.tx.wallet_type is WalletType.TREASURY and d.destination in ctx.settings.treasury_allowlist:
        return []
    wallet = ctx.tx.from_
    acct = ctx.state.accounts.get(wallet)
    if d.kind is Kind.NATIVE_TRANSFER:
        balance = acct.balance if acct else 0
    else:
        if acct is None or d.asset not in acct.token_balances:
            raise RuleInputMissing(f"token balance of {wallet} for {d.asset}")
        balance = acct.token_balances[d.asset]
    limit = ctx.settings.r3_max_single_dest_pct
    if balance == 0 or over_pct(d.amount, balance, limit):
        # No sandbox evidence of a drain here, only the Guard's balances: an honest large
        # withdrawal looks the same, so a human decides (principle 6). Sandbox evidence of a
        # hidden drain is the simulation-level R3, which blocks.
        return [Finding(
            rule_id="R3", action=Decision.NEEDS_HUMAN, subject=d.destination,
            title=f"Withdrawal is more than {limit:g}% of the wallet balance",
            detail=f"{amount(d.asset, d.amount)} of {amount(d.asset, balance)} "
                   f"({pct(d.amount, balance)}) to {d.destination}. Based on the Guard's balances "
                   "only, without evidence of a drain: a human confirms.")]
    return []


def r4_treasury_limit(ctx: RuleContext) -> list[Finding]:
    if ctx.tx.wallet_type is not WalletType.TREASURY:
        return []
    ctx.need("decoded", "settings")
    d, limit = ctx.decoded, ctx.settings.r4_treasury_limit_wei
    if d.kind in (Kind.ERC20_TRANSFER, Kind.ERC20_APPROVE):
        return [Finding(rule_id="R4", action=Decision.NEEDS_HUMAN, subject=d.destination,
                        title="Treasury token amount cannot be checked against a limit",
                        detail="Token limits are not configured yet, so a human must confirm.")]
    if ctx.tx.value > limit:
        return [Finding(rule_id="R4", action=Decision.NEEDS_HUMAN, subject=d.destination,
                        title="Treasury amount above limit",
                        detail=f"{amount('native', ctx.tx.value)} is above the treasury limit of "
                               f"{amount('native', limit)}.")]
    return []


def r5_treasury_allowlist(ctx: RuleContext) -> list[Finding]:
    if ctx.tx.wallet_type is not WalletType.TREASURY:
        return []
    ctx.need("decoded", "settings")
    d = ctx.decoded
    if d.destination in ctx.settings.treasury_allowlist:
        return []
    above = ctx.tx.value > ctx.settings.r4_treasury_limit_wei
    return [Finding(
        rule_id="R5", action=Decision.BLOCK if above else Decision.NEEDS_HUMAN, subject=d.destination,
        title="Treasury destination not on the allowlist" + (" and amount above limit" if above else ""),
        detail=f"{d.destination} is not an approved treasury destination"
               + (f"; {amount('native', ctx.tx.value)} is above the limit." if above else "."))]


def r6_outflow_window(ctx: RuleContext) -> list[Finding]:
    if ctx.tx.wallet_type is not WalletType.CUSTOMER_HOT or ctx.tx.value == 0:
        return []
    ctx.need("history", "settings")
    s = ctx.settings
    since = ctx.now - s.r6_window_minutes * MINUTE
    prior = ctx.history.outflow(ctx.tx.from_, "native", since, exclude=ctx.exclude_check)
    total = prior + ctx.tx.value
    if total > s.r6_outflow_limit_wei:
        return [Finding(
            rule_id="R6", action=Decision.BLOCK, subject=ctx.tx.from_,
            title=f"Wallet outflow in {s.r6_window_minutes} min above limit",
            detail=f"{amount('native', prior)} already sent + {amount('native', ctx.tx.value)} now = "
                   f"{amount('native', total)}, limit {amount('native', s.r6_outflow_limit_wei)}.")]
    return []


def r7_multi_chain_destination(ctx: RuleContext) -> list[Finding]:
    if ctx.tx.wallet_type is not WalletType.CUSTOMER_HOT:
        return []
    ctx.need("history", "decoded", "settings")
    d, s = ctx.decoded, ctx.settings
    if d.amount == 0:
        return []
    chains = ctx.history.chains_to(d.destination, ctx.now - s.r7_window_hours * HOUR,
                                   exclude=ctx.exclude_check) | {ctx.tx.chain_id}
    n, listed = len(chains), ", ".join(str(c) for c in sorted(chains))
    if n >= s.r7_freeze_chains:
        return [Finding(
            rule_id="R7", action=Decision.BLOCK, subject=d.destination, effect=Effect.FREEZE_DESTINATION,
            title=f"Same destination paid from {n} chains in {s.r7_window_hours} h: freeze it",
            detail=f"{d.destination} received from chains {listed}. Customers do not do this; "
                   "attackers draining many chains do.")]
    if n >= s.r7_delay_chains:
        return [Finding(
            rule_id="R7", action=Decision.DELAY, subject=d.destination,
            title=f"Same destination paid from {n} chains in {s.r7_window_hours} h",
            detail=f"{d.destination} received from chains {listed}. Holding for "
                   f"{s.delay_minutes} min in case more signals appear.")]
    return []


def r8_many_assets(ctx: RuleContext) -> list[Finding]:
    if ctx.tx.wallet_type is not WalletType.CUSTOMER_HOT:
        return []
    ctx.need("history", "decoded", "settings")
    d, s = ctx.decoded, ctx.settings
    if d.amount == 0:
        return []
    assets = ctx.history.assets_to(d.destination, ctx.now - s.r8_window_minutes * MINUTE,
                                   exclude=ctx.exclude_check)
    assets |= {f"{ctx.tx.chain_id}:{d.asset}"}
    if len(assets) >= s.r8_freeze_assets:
        return [Finding(
            rule_id="R8", action=Decision.BLOCK, subject=d.destination, effect=Effect.FREEZE_DESTINATION,
            title=f"{len(assets)} different assets to one address in {s.r8_window_minutes} min: freeze it",
            detail=f"{d.destination} received {len(assets)} distinct assets. A customer withdraws one "
                   "or two; a drain sends everything, one token at a time.")]
    return []


def c1_customer_shape(ctx: RuleContext) -> list[Finding]:
    if ctx.tx.wallet_type is not WalletType.CUSTOMER_HOT:
        return []
    ctx.need("decoded")
    d = ctx.decoded
    if d.kind in (Kind.NATIVE_TRANSFER, Kind.ERC20_TRANSFER):
        return []
    return [Finding(
        rule_id="C1", action=Decision.NEEDS_HUMAN, subject=ctx.tx.to,
        title=f"Customer hot wallet asked to sign a {d.kind.value.replace('_', ' ')}",
        detail="Customer withdrawals are plain transfers (native or a known token). "
               + (d.note + "." if d.note else "This shape needs a human."))]


REQUEST_RULES = [
    f4_global_stop,
    f1_frozen_destination,
    f2_paused_wallet,
    f3_safe_mode_chain,
    r2_authorizations,
    r3_request_outflow,
    r4_treasury_limit,
    r5_treasury_allowlist,
    r6_outflow_window,
    r7_multi_chain_destination,
    r8_many_assets,
    c1_customer_shape,
]
