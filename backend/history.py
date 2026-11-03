"""SQLite history: every check, plus the latched breaker state.

Time is always passed in by the caller (an injected clock), never read here, so all
window queries are deterministic in tests.

Counting conventions:
- outflow (R6) counts checks that may move funds: ALLOW, DELAY, NEEDS_HUMAN.
- chains / assets per destination (R7, R8) and authorization nonces (R2) count every
  attempt, also blocked ones: repeated attempts are themselves the attack signal.
"""

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAY_MOVE_FUNDS = ("ALLOW", "DELAY", "NEEDS_HUMAN")

SCHEMA = """
CREATE TABLE IF NOT EXISTS checks (
    id           TEXT PRIMARY KEY,
    ts           REAL NOT NULL,
    wallet       TEXT NOT NULL,
    chain_id     INTEGER NOT NULL,
    wallet_type  TEXT NOT NULL,
    kind         TEXT NOT NULL,
    destination  TEXT NOT NULL,
    asset        TEXT NOT NULL,
    amount       TEXT NOT NULL,
    path         TEXT NOT NULL,
    path_reasons TEXT NOT NULL,
    verdict      TEXT NOT NULL,
    findings     TEXT NOT NULL,
    timings      TEXT NOT NULL,
    llm          TEXT,
    request      TEXT NOT NULL,
    release_at   REAL
);
CREATE INDEX IF NOT EXISTS checks_wallet ON checks(wallet, ts);
CREATE INDEX IF NOT EXISTS checks_dest ON checks(destination, ts);

CREATE TABLE IF NOT EXISTS auth_nonces (
    check_id  TEXT NOT NULL,
    ts        REAL NOT NULL,
    authority TEXT NOT NULL,
    chain_id  INTEGER NOT NULL,
    nonce     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS auth_nonces_authority ON auth_nonces(authority, ts);

CREATE TABLE IF NOT EXISTS frozen_destinations (
    address  TEXT PRIMARY KEY,
    reason   TEXT NOT NULL,
    ts       REAL NOT NULL,
    check_id TEXT
);

CREATE TABLE IF NOT EXISTS paused_wallets (
    wallet   TEXT PRIMARY KEY,
    reason   TEXT NOT NULL,
    ts       REAL NOT NULL,
    check_id TEXT
);

CREATE TABLE IF NOT EXISTS breaker_events (
    ts      REAL NOT NULL,
    action  TEXT NOT NULL,
    subject TEXT NOT NULL,
    reason  TEXT NOT NULL,
    actor   TEXT NOT NULL
);
"""


@dataclass
class CheckRecord:
    id: str
    ts: float
    wallet: str
    chain_id: int
    wallet_type: str
    kind: str
    destination: str
    asset: str
    amount: int
    path: str
    path_reasons: list[str]
    verdict: str
    findings: list[dict[str, Any]]
    timings: dict[str, float]
    llm: dict[str, Any] | None
    request: dict[str, Any]
    release_at: float | None = None
    auth_nonces: list[tuple[str, int, int]] = field(default_factory=list)  # (authority, chain_id, nonce)


class History:
    def __init__(self, path: str | Path = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock, self.conn:
            self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # ---- writes ----------------------------------------------------------------

    def record_check(self, r: CheckRecord) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO checks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r.id, r.ts, r.wallet, r.chain_id, r.wallet_type, r.kind, r.destination, r.asset,
                 str(r.amount), r.path, json.dumps(r.path_reasons), r.verdict, json.dumps(r.findings),
                 json.dumps(r.timings), json.dumps(r.llm) if r.llm is not None else None,
                 json.dumps(r.request), r.release_at),
            )
            self.conn.executemany(
                "INSERT INTO auth_nonces VALUES (?,?,?,?,?)",
                [(r.id, r.ts, a, c, n) for a, c, n in r.auth_nonces],
            )

    def freeze(self, address: str, reason: str, now: float, check_id: str | None = None,
               actor: str = "guard") -> bool:
        """Latch a destination freeze. Returns True if it was not frozen before."""
        with self.lock, self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO frozen_destinations VALUES (?,?,?,?)", (address, reason, now, check_id))
            self.conn.execute("INSERT INTO breaker_events VALUES (?,?,?,?,?)",
                              (now, "freeze_destination", address, reason, actor))
            return cur.rowcount == 1

    def pause(self, wallet: str, reason: str, now: float, check_id: str | None = None,
              actor: str = "guard") -> bool:
        """Latch a wallet pause. Returns True if it was not paused before."""
        with self.lock, self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO paused_wallets VALUES (?,?,?,?)", (wallet, reason, now, check_id))
            self.conn.execute("INSERT INTO breaker_events VALUES (?,?,?,?,?)",
                              (now, "pause_wallet", wallet, reason, actor))
            return cur.rowcount == 1

    def unfreeze(self, address: str, actor: str, now: float, reason: str = "manual reset") -> bool:
        with self.lock, self.conn:
            cur = self.conn.execute("DELETE FROM frozen_destinations WHERE address = ?", (address,))
            self.conn.execute("INSERT INTO breaker_events VALUES (?,?,?,?,?)",
                              (now, "reset_destination", address, reason, actor))
            return cur.rowcount == 1

    def unpause(self, wallet: str, actor: str, now: float, reason: str = "manual reset") -> bool:
        with self.lock, self.conn:
            cur = self.conn.execute("DELETE FROM paused_wallets WHERE wallet = ?", (wallet,))
            self.conn.execute("INSERT INTO breaker_events VALUES (?,?,?,?,?)",
                              (now, "reset_wallet", wallet, reason, actor))
            return cur.rowcount == 1

    # ---- reads -----------------------------------------------------------------

    def _rows(self, sql: str, args: tuple) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def outflow(self, wallet: str, asset: str, since: float) -> int:
        marks = ",".join("?" * len(MAY_MOVE_FUNDS))
        rows = self._rows(
            f"SELECT amount FROM checks WHERE wallet = ? AND asset = ? AND ts >= ? AND verdict IN ({marks})",
            (wallet, asset, since, *MAY_MOVE_FUNDS))
        return sum(int(r["amount"]) for r in rows)

    def chains_to(self, destination: str, since: float) -> set[int]:
        """Chains from which value was sent (or attempted) to `destination`."""
        rows = self._rows(
            "SELECT DISTINCT chain_id FROM checks WHERE destination = ? AND ts >= ? AND amount != '0'",
            (destination, since))
        return {r["chain_id"] for r in rows}

    def assets_to(self, destination: str, since: float) -> set[str]:
        """Distinct assets as 'chain_id:asset' (the same token address on two chains is two assets)."""
        rows = self._rows(
            "SELECT DISTINCT chain_id, asset FROM checks WHERE destination = ? AND ts >= ? AND amount != '0'",
            (destination, since))
        return {f"{r['chain_id']}:{r['asset']}" for r in rows}

    def recent_auth_nonces(self, authority: str, since: float) -> list[tuple[int, int]]:
        rows = self._rows("SELECT chain_id, nonce FROM auth_nonces WHERE authority = ? AND ts >= ?",
                          (authority, since))
        return [(r["chain_id"], r["nonce"]) for r in rows]

    def is_frozen(self, address: str) -> dict[str, Any] | None:
        rows = self._rows("SELECT * FROM frozen_destinations WHERE address = ?", (address,))
        return dict(rows[0]) if rows else None

    def is_paused(self, wallet: str) -> dict[str, Any] | None:
        rows = self._rows("SELECT * FROM paused_wallets WHERE wallet = ?", (wallet,))
        return dict(rows[0]) if rows else None

    def frozen(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._rows("SELECT * FROM frozen_destinations ORDER BY ts", ())]

    def paused(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._rows("SELECT * FROM paused_wallets ORDER BY ts", ())]

    def events(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._rows("SELECT * FROM breaker_events ORDER BY ts, rowid", ())]

    def checks(self) -> list[dict[str, Any]]:
        out = []
        for r in self._rows("SELECT * FROM checks ORDER BY ts, rowid", ()):
            d = dict(r)
            for k in ("path_reasons", "findings", "timings", "request"):
                d[k] = json.loads(d[k])
            d["llm"] = json.loads(d["llm"]) if d["llm"] else None
            d["amount"] = int(d["amount"])
            out.append(d)
        return out
