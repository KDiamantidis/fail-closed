"""Structured reports shared by the CLI and the API (the UI renders these as plain text)."""

from decimal import Decimal
from typing import Any

from history import History
from scenario import Scenario

REAL_ATTACK_CHAINS = 10  # Mercado Bitcoin (2 Nov): the same address on 10 chains


def _units(amount: int) -> str:
    return f"{(Decimal(amount) / Decimal(10**18)).normalize():f}"


def blast_radius_data(history: History, sc: Scenario) -> list[dict[str, Any]]:
    """For each frozen destination of this scenario: what was ALLOWed to it before the freeze
    (would have been signed), what DELAY was holding (never released: the freeze is a new
    signal), and which chains were reached, compared with the real attack's 10 chains."""
    txs = [r.tx() for r in sc.requests if not r.is_action]
    addrs = {t.to for t in txs} | {"0x" + t.data[34:74] for t in txs if t.data.startswith("0xa9059cbb")}
    out = []
    for row in history.frozen():
        addr = row["address"]
        if addr not in addrs:
            continue
        allowed: dict[tuple[int, str], int] = {}
        held: dict[tuple[int, str], int] = {}
        chains_reached: set[int] = set()
        for c in history.checks_to(addr):
            chains_reached.add(c["chain_id"])
            if c["ts"] > row["ts"]:
                continue
            bucket = allowed if c["verdict"] == "ALLOW" else held if c["verdict"] == "DELAY" else None
            if bucket is not None:
                key = (c["chain_id"], c["asset"])
                bucket[key] = bucket.get(key, 0) + c["amount"]

        def rows(bucket):
            items = sorted(bucket.items(), key=lambda kv: (kv[0][0], kv[0][1] != "native", kv[0][1]))
            return [{"chain_id": chain, "asset": asset,
                     "asset_name": "ETH" if asset == "native" else (sc.label(asset) or asset),
                     "amount": str(amt), "units": _units(amt)} for (chain, asset), amt in items]

        def totals(bucket):
            native = sum(v for (_, a), v in bucket.items() if a == "native")
            tokens = sum(v for (_, a), v in bucket.items() if a != "native")
            return {"native_units": _units(native), "token_units": _units(tokens),
                    "token_types": len({a for (_, a) in bucket if a != "native"}),
                    "chains": sorted({c for (c, _) in bucket})}

        out.append({
            "address": addr, "label": sc.label(addr), "frozen_by": row["reason"].split(":")[0],
            "frozen_reason": row["reason"], "frozen_at": row["ts"],
            "allowed": rows(allowed), "allowed_totals": totals(allowed),
            "held": rows(held), "held_totals": totals(held),
            "chains_reached": sorted(chains_reached), "real_attack_chains": REAL_ATTACK_CHAINS,
        })
    return out
