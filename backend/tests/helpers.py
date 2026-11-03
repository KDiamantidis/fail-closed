HOT = "0x" + "a1" * 20
BOB = "0x" + "b0" * 20
SWEEPER = "0x" + "5e" * 20


def tx(**overrides) -> dict:
    base = {
        "wallet_type": "customer_hot",
        "chain_id": 1,
        "from": HOT,
        "to": BOB,
        "data": "0x",
        "value": 10**18,
    }
    base.update(overrides)
    return base
