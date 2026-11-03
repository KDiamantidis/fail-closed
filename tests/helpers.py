ALICE = "0x" + "a1" * 20
BOB = "0x" + "b0" * 20
TOKEN = "0x" + "70" * 20


def approve_calldata(spender: str, amount: int, selector: str = "0x095ea7b3") -> str:
    return selector + spender[2:].rjust(64, "0") + format(amount, "064x")


def tx(**overrides) -> dict:
    base = {"to": TOKEN, "from": ALICE, "data": "0x", "value": 0, "chain_id": 1}
    base.update(overrides)
    return base
