"""Deterministic rules. Each rule takes the tx and returns findings. No LLM involved."""

from app.models import Finding, Severity, TxRequest

# ERC-20 approve(address,uint256) and the same-layout increaseAllowance(address,uint256)
APPROVE_SELECTORS = {
    "0x095ea7b3": "approve",
    "0x39509351": "increaseAllowance",
}


def check_large_approval(tx: TxRequest, threshold: int) -> list[Finding]:
    """Flag approvals at or above `threshold` (default 2**128).

    A fixed threshold catches max-style values (2**256-1, 2**255, ...) but not
    every "effectively unlimited" amount: token supplies differ wildly
    (SHIB is ~5.9e32 base units, far below 2**128).

    TODO: once we have chain state (fork / demo contracts), replace or extend this
    with: amount > owner's token balance, or amount > token totalSupply.
    """
    selector = tx.data[:10]
    name = APPROVE_SELECTORS.get(selector)
    if name is None:
        return []

    args = tx.data[10:]
    if len(args) < 128:  # two 32-byte words
        return [
            Finding(
                rule_id="approval.malformed",
                severity=Severity.HIGH,
                blocking=True,
                title=f"Malformed {name} calldata",
                detail="Calldata is too short to decode; cannot tell what is being approved.",
            )
        ]

    spender = "0x" + args[24:64]
    amount = int(args[64:128], 16)
    if amount < threshold:
        return []

    shown = "2^256-1 (max uint256)" if amount == 2**256 - 1 else str(amount)
    return [
        Finding(
            rule_id="approval.unlimited",
            severity=Severity.CRITICAL,
            blocking=True,
            title=f"Unlimited token {name}",
            detail=f"Grants {spender} the right to spend {shown} tokens of {tx.to} from {tx.from_}.",
        )
    ]
