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

CREATE TABLE IF NOT EXISTS held (
    id          TEXT PRIMARY KEY,   -- the check id
    kind        TEXT NOT NULL,      -- needs_human | delay
    ts          REAL NOT NULL,
    request     TEXT NOT NULL,
    rule_ids    TEXT NOT NULL,      -- rules that caused the hold (a re-check may repeat these)
    reasons     TEXT NOT NULL,
    status      TEXT NOT NULL,      -- pending | approved | rejected | released | blocked
    release_at  REAL,
    decided_by  TEXT,
    decided_at  REAL,
    outcome     TEXT,
    approval_id TEXT,
    signed      INTEGER
);

CREATE TABLE IF NOT EXISTS redteam_reports (
    id     TEXT PRIMARY KEY,
    ts     REAL NOT NULL,
    report TEXT NOT NULL
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
            if cur.rowcount != 1:
                return False  # already frozen: latched, first reason kept
            self.conn.execute("INSERT INTO breaker_events VALUES (?,?,?,?,?)",
                              (now, "freeze_destination", address, reason, actor))
            return True

    def pause(self, wallet: str, reason: str, now: float, check_id: str | None = None,
              actor: str = "guard") -> bool:
        """Latch a wallet pause. Returns True if it was not paused before."""
        with self.lock, self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO paused_wallets VALUES (?,?,?,?)", (wallet, reason, now, check_id))
            if cur.rowcount != 1:
                return False  # already paused: latched, first reason kept
            self.conn.execute("INSERT INTO breaker_events VALUES (?,?,?,?,?)",
                              (now, "pause_wallet", wallet, reason, actor))
            return True

    def unfreeze(self, address: str, actor: str, now: float, reason: str = "manual reset") -> bool:
        with self.lock, self.conn:
            cur = self.conn.execute("DELETE FROM frozen_destinations WHERE address = ?", (address,))
            if cur.rowcount != 1:
                return False
            self.conn.execute("INSERT INTO breaker_events VALUES (?,?,?,?,?)",
                              (now, "reset_destination", address, reason, actor))
            return True

    def unpause(self, wallet: str, actor: str, now: float, reason: str = "manual reset") -> bool:
        with self.lock, self.conn:
            cur = self.conn.execute("DELETE FROM paused_wallets WHERE wallet = ?", (wallet,))
            if cur.rowcount != 1:
                return False
            self.conn.execute("INSERT INTO breaker_events VALUES (?,?,?,?,?)",
                              (now, "reset_wallet", wallet, reason, actor))
            return True

    # ---- reads -----------------------------------------------------------------

    def _rows(self, sql: str, args: tuple) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def outflow(self, wallet: str, asset: str, since: float, exclude: str | None = None) -> int:
        marks = ",".join("?" * len(MAY_MOVE_FUNDS))
        rows = self._rows(
            f"SELECT amount FROM checks WHERE wallet = ? AND asset = ? AND ts >= ? AND verdict IN ({marks})"
            " AND id != ?", (wallet, asset, since, *MAY_MOVE_FUNDS, exclude or ""))
        return sum(int(r["amount"]) for r in rows)

    def chains_to(self, destination: str, since: float, exclude: str | None = None) -> set[int]:
        """Chains from which value was sent (or attempted) to `destination`."""
        rows = self._rows(
            "SELECT DISTINCT chain_id FROM checks WHERE destination = ? AND ts >= ? AND amount != '0'"
            " AND id != ?", (destination, since, exclude or ""))
        return {r["chain_id"] for r in rows}

    def assets_to(self, destination: str, since: float, exclude: str | None = None) -> set[str]:
        """Distinct assets as 'chain_id:asset' (the same token address on two chains is two assets)."""
        rows = self._rows(
            "SELECT DISTINCT chain_id, asset FROM checks WHERE destination = ? AND ts >= ? AND amount != '0'"
            " AND id != ?", (destination, since, exclude or ""))
        return {f"{r['chain_id']}:{r['asset']}" for r in rows}

    def recent_auth_nonces(self, authority: str, since: float,
                           exclude: str | None = None) -> list[tuple[int, int]]:
        rows = self._rows(
            "SELECT chain_id, nonce FROM auth_nonces WHERE authority = ? AND ts >= ? AND check_id != ?",
            (authority, since, exclude or ""))
        return [(r["chain_id"], r["nonce"]) for r in rows]

    def checks_to(self, destination: str) -> list[dict[str, Any]]:
        """Value-moving checks to one destination, oldest first (for the blast radius)."""
        rows = self._rows("SELECT ts, chain_id, asset, amount, verdict FROM checks "
                          "WHERE destination = ? AND amount != '0' ORDER BY ts, rowid", (destination,))
        return [{**dict(r), "amount": int(r["amount"])} for r in rows]

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

    # ---- held requests (NEEDS_HUMAN waits for a human, DELAY waits for release_at) ----

    def hold(self, check_id: str, kind: str, ts: float, request: dict, rule_ids: list[str],
             reasons: list[str], release_at: float | None = None) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO held (id, kind, ts, request, rule_ids, reasons, status, release_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
                (check_id, kind, ts, json.dumps(request), json.dumps(rule_ids), json.dumps(reasons), release_at))

    def _held_row(self, r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        for k in ("request", "rule_ids", "reasons"):
            d[k] = json.loads(d[k])
        d["signed"] = None if d["signed"] is None else bool(d["signed"])
        return d

    def held(self, status: str | None = "pending", kind: str | None = None) -> list[dict[str, Any]]:
        rows = self._rows("SELECT * FROM held WHERE (? IS NULL OR status = ?) AND (? IS NULL OR kind = ?) "
                          "ORDER BY ts, rowid", (status, status, kind, kind))
        return [self._held_row(r) for r in rows]

    def get_held(self, check_id: str) -> dict[str, Any] | None:
        rows = self._rows("SELECT * FROM held WHERE id = ?", (check_id,))
        return self._held_row(rows[0]) if rows else None

    def due_delays(self, now: float) -> list[dict[str, Any]]:
        rows = self._rows("SELECT * FROM held WHERE kind = 'delay' AND status = 'pending' AND release_at <= ? "
                          "ORDER BY release_at, rowid", (now,))
        return [self._held_row(r) for r in rows]

    def resolve_held(self, check_id: str, status: str, outcome: str, decided_by: str, now: float,
                     approval_id: str | None = None, signed: bool | None = None) -> bool:
        """pending -> a final status, exactly once (False if it was already decided)."""
        with self.lock, self.conn:
            cur = self.conn.execute(
                "UPDATE held SET status = ?, outcome = ?, decided_by = ?, decided_at = ?, approval_id = ?, "
                "signed = ? WHERE id = ? AND status = 'pending'",
                (status, outcome, decided_by, now, approval_id, None if signed is None else int(signed), check_id))
            return cur.rowcount == 1

    def save_redteam(self, run_id: str, ts: float, report: dict[str, Any]) -> None:
        with self.lock, self.conn:
            self.conn.execute("INSERT INTO redteam_reports VALUES (?, ?, ?)", (run_id, ts, json.dumps(report)))

    def last_redteam(self) -> dict[str, Any] | None:
        rows = self._rows("SELECT * FROM redteam_reports ORDER BY ts DESC, rowid DESC LIMIT 1", ())
        return {"id": rows[0]["id"], "ts": rows[0]["ts"], **json.loads(rows[0]["report"])} if rows else None

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """Newest first."""
        return list(reversed(self.checks()))[:limit]

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
