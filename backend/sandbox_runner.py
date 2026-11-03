"""Run one signing request in a fresh, locked-down Anvil container.

One container per check: no network, read-only root, tmpfs for temp, memory/CPU/pids
limits, all capabilities dropped, no-new-privileges, no env vars or secrets passed in.
The host drives Anvil with `docker exec` + `cast` (the container has no network, so
there is no port to talk to). The container is always destroyed, also on error/timeout.

Content is validated, exit codes are not trusted: a missing receipt, empty state diff
or mismatched authorizationList raises SandboxError, which decide() turns into R0.

Fees are zero (base fee and gas price 0) so balance changes are value movements only.

EIP-7702, two ways:
- "type4": scenario wallets that are Anvil test accounts. Authorizations are signed in
  the sandbox with the public test mnemonic and sent in a real type-4 transaction.
- "effect": every other wallet. The Guard holds no wallet keys and is asked before the
  signature exists, so it applies the effect instead: anvil_setCode(0xef0100||delegate)
  plus the nonce bump, following EIP-7702 nonce rules, then runs the rest of the tx.
  The diff is merged so both modes report the same state change.

After a delegation the runner probes the wallet: outside callers (addresses hard-coded
in the delegate's code first, then a random account) call it with empty calldata and
common sweep selectors, and any funds that move are recorded as evidence for R3.
"""

import copy
import json
import logging
import re
import secrets
import shlex
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any

from app.config import Settings
from app.models import SimulationResult, TxRequest
from decode import authority_of
from state_provider import StartingState

log = logging.getLogger(__name__)

RPC = "http://127.0.0.1:8545"
# Public Anvil/Hardhat test mnemonic. Its keys are known to everyone; sandbox use only.
ANVIL_MNEMONIC = "test test test test test test test test test test test junk"
ANVIL_ACCOUNTS = [
    "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266",
    "0x70997970c51812dc3a010c7d01b50e0d17dc79c8",
    "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc",
    "0x90f79bf6eb2c4f870365e785982e1f101e93b906",
    "0x15d34aaf54267db7d7c367839aaf71a00a2c6a65",
    "0x9965507d1a55bcc2695c58ba16fb37d819b0a4dc",
    "0x976ea74026e726554db657fa54763abd0c3a0aa9",
    "0x14dc79964da2c08b23698b3d3cc7ca32193d9955",
    "0x23618e81e3f5cdf7f54c3d65f7fbc0abf5b21e8f",
    "0xa0ee7a142d267c1f36714e4a8f75612f20a79720",
]
ARTIFACT_DIR = "/sandbox/artifacts"
LABEL = "fail-closed.sandbox"
SEP = "@@FAIL-CLOSED-SEP@@"
TX_GAS_LIMIT = 5_000_000  # fixed so a reverting tx is still mined; never used by rules
READY_TIMEOUT = 15.0
RECEIPT_TRIES = 20
DELEGATION_PREFIX = "0xef0100"
ZERO_ADDRESS = "0x" + "00" * 20
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TOKEN_BALANCE_SLOT = 0  # MockToken layout: mapping(address => uint256) balanceOf at slot 0
PROBE_CALLS = [
    ("0x", "empty call"),
    ("0x35faa416", "sweep()"),
    ("0x3ccfd60b", "withdraw()"),
    ("0x61461954", "execute()"),
]
MAX_EMBEDDED_CALLERS = 3
_HASH = re.compile(r"0x[0-9a-fA-F]{64}")
_ZERO_FEES = ["--gas-price", "0", "--priority-gas-price", "0"]


class SandboxError(Exception):
    """The sandbox could not produce a trustworthy result. Always means R0 / BLOCK."""


def anvil_args(chain_id: int) -> list[str]:
    return [
        "--hardfork", "prague",
        "--chain-id", str(chain_id),
        "--host", "127.0.0.1",
        "--port", "8545",
        "--auto-impersonate",
        "--base-fee", "0",
        "--gas-price", "0",
    ]


def container_options(settings: Settings) -> dict[str, Any]:
    """docker run options. Deliberately no volumes, no env, no ports, no extra privileges."""
    return {
        "detach": True,
        "auto_remove": False,  # we remove it ourselves in finally, so we can inspect first
        "network_mode": "none",
        "read_only": True,
        "tmpfs": {
            "/tmp": "rw,nosuid,nodev,noexec,size=64m,mode=1777",
            "/home/foundry": "rw,nosuid,nodev,noexec,size=16m,uid=1000,gid=1000,mode=0700",
        },
        "mem_limit": settings.sandbox_memory,
        "memswap_limit": settings.sandbox_memory,  # no swap on top
        "nano_cpus": int(settings.sandbox_cpus * 1_000_000_000),
        "pids_limit": settings.sandbox_pids,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges"],
        "privileged": False,
        "environment": {},
        "labels": {LABEL: "1"},
    }


def applied_limits(attrs: dict[str, Any]) -> dict[str, Any]:
    """The limits Docker actually applied, from `docker inspect`."""
    hc = attrs.get("HostConfig", {}) or {}
    cfg = attrs.get("Config", {}) or {}
    return {
        "NetworkMode": hc.get("NetworkMode"),
        "ReadonlyRootfs": hc.get("ReadonlyRootfs"),
        "Memory": hc.get("Memory"),
        "MemorySwap": hc.get("MemorySwap"),
        "NanoCpus": hc.get("NanoCpus"),
        "PidsLimit": hc.get("PidsLimit"),
        "CapDrop": hc.get("CapDrop"),
        "CapAdd": hc.get("CapAdd"),
        "SecurityOpt": hc.get("SecurityOpt"),
        "Privileged": hc.get("Privileged"),
        "Tmpfs": hc.get("Tmpfs"),
        "Binds": hc.get("Binds"),
        "User": cfg.get("User"),
        "Env": cfg.get("Env"),
        "Image": cfg.get("Image"),
    }


def delegation_mode(tx: TxRequest) -> str | None:
    """type4 only when every key involved is a public sandbox test key; otherwise effect."""
    auths = tx.authorization_list
    if not auths:
        return None
    if tx.from_ in ANVIL_ACCOUNTS and all(
        not a.signed and authority_of(a, tx) in ANVIL_ACCOUNTS for a in auths
    ):
        return "type4"
    return "effect"


def push20_addresses(code: str) -> list[str]:
    """Addresses pushed as constants (PUSH20) in EVM bytecode, in order, without duplicates."""
    try:
        b = bytes.fromhex(code[2:] if code.startswith("0x") else code)
    except ValueError:
        return []
    out: list[str] = []
    i = 0
    while i < len(b):
        op = b[i]
        if 0x60 <= op <= 0x7F:
            n = op - 0x5F
            if op == 0x73 and i + 21 <= len(b):
                addr = "0x" + b[i + 1:i + 21].hex()
                if addr not in out:
                    out.append(addr)
            i += 1 + n
        else:
            i += 1
    return out


def merge_diffs(first: dict, second: dict) -> dict:
    """Combine two consecutive prestate diffs (diffMode) into one.

    diffMode: `pre` holds the full prior state of touched accounts (zero nonce and empty
    code omitted), `post` only the fields that changed.
    """
    pre = copy.deepcopy(second.get("pre", {}))
    for addr, fields in first.get("pre", {}).items():
        pre.setdefault(addr, {}).update(fields)
    for addr, fields in first.get("post", {}).items():
        for f in fields:
            if f not in first.get("pre", {}).get(addr, {}):
                pre.get(addr, {}).pop(f, None)  # it was zero / empty before `first`
    post: dict = {}
    for addr in list(first.get("post", {})) + list(second.get("post", {})):
        post[addr] = {**first.get("post", {}).get(addr, {}), **second.get("post", {}).get(addr, {})}
    for addr in list(post):
        for f, v in list(post[addr].items()):
            if pre.get(addr, {}).get(f) == v:
                del post[addr][f]
        if not post[addr]:
            del post[addr]
    return {"pre": {a: v for a, v in pre.items() if v or a in post}, "post": post}


def _rpc(method: str, params: list[Any]) -> list[str]:
    return ["cast", "rpc", "-r", RPC, "--raw", method, json.dumps(params)]


def _hex32(v: str) -> str:
    return "0x" + v[2:].rjust(64, "0")


def _int(v: Any) -> int:
    return int(v, 16) if isinstance(v, str) else int(v or 0)


def validate_result(
    tx: TxRequest, tx_hash: str, receipt: Any, mined_tx: Any, diff: Any
) -> None:
    """Content checks. Raise SandboxError on anything missing, empty or inconsistent."""
    if not isinstance(receipt, dict) or not receipt:
        raise SandboxError("missing receipt")
    if str(receipt.get("transactionHash", "")).lower() != tx_hash.lower():
        raise SandboxError("receipt is for a different transaction")
    if "status" not in receipt:
        raise SandboxError("receipt has no status")
    if not isinstance(mined_tx, dict) or str(mined_tx.get("hash", "")).lower() != tx_hash.lower():
        raise SandboxError("mined transaction not found")
    if not isinstance(diff, dict) or not isinstance(diff.get("pre"), dict) \
            or not isinstance(diff.get("post"), dict) or not diff["post"]:
        raise SandboxError("empty state diff")
    want = len(tx.authorization_list or [])
    got = mined_tx.get("authorizationList") or []
    if not isinstance(got, list) or len(got) != want:
        raise SandboxError(f"authorizationList mismatch: request has {want}, mined tx has {len(got) if isinstance(got, list) else '?'}")


@dataclass
class _Run:
    name: str
    container: Any = None
    cancelled: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)
    timings: dict[str, float] = field(default_factory=dict)
    limits: dict[str, Any] = field(default_factory=dict)


class SandboxRunner:
    def __init__(self, settings: Settings, client: Any = None, clock=time.perf_counter):
        self.settings = settings
        self._client = client
        self.clock = clock

    @property
    def client(self) -> Any:
        if self._client is None:
            import docker  # imported lazily so unit tests do not need a daemon

            self._client = docker.from_env()
        return self._client

    # ---- public ---------------------------------------------------------------

    def run(self, tx: TxRequest, state: StartingState, force_mode: str | None = None) -> SimulationResult:
        """Simulate `tx` on `state`. Returns an ok result or raises SandboxError.

        `force_mode="effect"` simulates a delegation by its effect even for test accounts
        (used to prove both modes agree)."""
        run = _Run(name=f"fc-sandbox-{uuid.uuid4().hex[:12]}")
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sandbox")
        timeout = self.settings.sandbox_timeout_seconds
        try:
            future = pool.submit(self._run, run, tx, state, force_mode)
            try:
                result = future.result(timeout=timeout)
            except FutureTimeout:
                raise SandboxError(f"timeout after {timeout:g}s") from None
            except SandboxError:
                raise
            except Exception as e:  # docker errors, JSON errors, ...: all fail closed
                raise SandboxError(f"{type(e).__name__}: {e}") from e
        finally:
            t0 = self.clock()
            self._destroy(run)
            run.timings["destroy"] = self.clock() - t0
            pool.shutdown(wait=False, cancel_futures=True)
        result.timings = run.timings
        return result

    # ---- steps ----------------------------------------------------------------

    def _run(self, run: _Run, tx: TxRequest, state: StartingState, force_mode: str | None) -> SimulationResult:
        t = self.clock
        mode = delegation_mode(tx)
        if mode and force_mode:
            mode = force_mode

        t0 = t()
        container = self.client.containers.run(
            self.settings.sandbox_image,
            command=anvil_args(tx.chain_id),
            name=run.name,
            **container_options(self.settings),
        )
        with run.lock:
            run.container = container
            cancelled = run.cancelled
        if cancelled:  # timed out while the container was being created
            self._remove(container)
            raise SandboxError("cancelled")
        container.reload()
        run.limits = applied_limits(container.attrs)
        self._wait_ready(container)
        run.timings["container_start"] = t() - t0

        t0 = t()
        self._inject(container, state)
        run.timings["state_inject"] = t() - t0

        t0 = t()
        synthetic: dict | None = None
        if mode == "effect":
            synthetic = self._apply_delegation_effect(container, tx)
            tx_hash = self._send(container, tx, with_auths=False, impersonate=True)
        else:
            tx_hash = self._send(container, tx, with_auths=(mode == "type4"), impersonate=False)
        run.timings["tx"] = t() - t0

        t0 = t()
        receipt, mined_tx, diff = self._collect(container, tx_hash)
        if synthetic is not None and isinstance(diff, dict):
            diff = merge_diffs(synthetic, diff)
            if isinstance(mined_tx, dict):
                mined_tx = {**mined_tx, "authorizationList": [
                    {"chainId": hex(a.chain_id), "address": a.address, "nonce": hex(a.nonce),
                     "authority": authority_of(a, tx), "simulated": True}
                    for a in tx.authorization_list or []]}
        run.timings["trace"] = t() - t0

        validate_result(tx, tx_hash, receipt, mined_tx, diff)

        t0 = t()
        probe = self._probe(container, diff)
        run.timings["probe"] = t() - t0

        return SimulationResult(
            ok=True, receipt=receipt, state_diff=diff, tx=mined_tx, sandbox=run.limits,
            delegation_mode=mode, probe=probe,
        )

    def _wait_ready(self, c: Any) -> None:
        deadline = self.clock() + READY_TIMEOUT
        while self.clock() < deadline:
            res = c.exec_run(["cast", "block-number", "-r", RPC], demux=True)
            if res.exit_code == 0:
                return
            time.sleep(0.05)
        raise SandboxError("anvil did not become ready")

    def _inject(self, c: Any, state: StartingState) -> None:
        artifacts = {
            s.code.split(":", 1)[1]
            for s in state.accounts.values()
            if s.code and s.code.startswith("artifact:")
        }
        code_of: dict[str, str] = {}
        for name in sorted(artifacts):  # names are validated [A-Za-z0-9_]+
            code = self._exec(c, ["cat", f"{ARTIFACT_DIR}/{name}.hex"], f"read artifact {name}").strip()
            if not re.fullmatch(r"0x([0-9a-f]{2})+", code):
                raise SandboxError(f"artifact {name} is empty or invalid")
            code_of[f"artifact:{name}"] = code

        # balanceOf slots for token balances (MockToken layout), computed in the sandbox
        holdings = [(holder, token, amt) for holder, s in state.accounts.items()
                    for token, amt in s.token_balances.items()]
        slots: list[str] = []
        if holdings:
            slots = self._batch(c, [["cast", "index", "address", h, str(TOKEN_BALANCE_SLOT)]
                                    for h, _, _ in holdings], "token balance slots")
            for s in slots:
                if not re.fullmatch(r"0x[0-9a-f]{64}", s):
                    raise SandboxError("cast index returned an invalid slot")

        cmds: list[list[str]] = []
        expect: list[tuple[list[str], str]] = []
        for addr, s in state.accounts.items():
            cmds.append(_rpc("anvil_setBalance", [addr, hex(s.balance)]))
            cmds.append(_rpc("anvil_setNonce", [addr, hex(s.nonce)]))
            expect.append((_rpc("eth_getBalance", [addr, "latest"]), hex(s.balance)))
            expect.append((_rpc("eth_getTransactionCount", [addr, "latest"]), hex(s.nonce)))
            if s.code is not None:
                code = code_of.get(s.code, s.code)
                cmds.append(_rpc("anvil_setCode", [addr, code]))
                expect.append((_rpc("eth_getCode", [addr, "latest"]), code))
            for slot, value in s.storage.items():
                cmds.append(_rpc("anvil_setStorageAt", [addr, _hex32(slot), _hex32(value)]))
                expect.append((_rpc("eth_getStorageAt", [addr, _hex32(slot), "latest"]), _hex32(value)))
        for (holder, token, amt), slot in zip(holdings, slots, strict=True):
            cmds.append(_rpc("anvil_setStorageAt", [token, slot, _hex32(hex(amt))]))
            expect.append((_rpc("eth_getStorageAt", [token, slot, "latest"]), _hex32(hex(amt))))
        if not cmds:
            return
        self._batch(c, cmds, "state inject")
        self._verify(c, expect, "state inject")

    def _verify(self, c: Any, expect: list[tuple[list[str], str]], what: str) -> None:
        """Read back: never assume a state change worked."""
        got = self._batch(c, [e[0] for e in expect], f"{what} read-back")
        for (cmd, want), out in zip(expect, got, strict=True):
            method, value = cmd[5], json.loads(out)
            if not isinstance(value, str):
                ok = False
            elif method == "eth_getCode":
                ok = value.lower() == want.lower()
            else:
                ok = int(value, 16) == int(want, 16)
            if not ok:
                raise SandboxError(f"{what} mismatch: {method} {cmd[6]}")

    def _apply_delegation_effect(self, c: Any, tx: TxRequest) -> dict:
        """Apply the EIP-7702 effect without signatures. Returns the synthetic diff."""
        auths = tx.authorization_list or []
        for a in auths:
            if a.signed and a.authority is None:
                raise SandboxError("cannot determine the authority of a pre-signed authorization")
        authorities = list(dict.fromkeys(authority_of(a, tx) for a in auths))
        reads = []
        for w in authorities:
            reads += [_rpc("eth_getBalance", [w, "latest"]), _rpc("eth_getTransactionCount", [w, "latest"]),
                      _rpc("eth_getCode", [w, "latest"])]
        vals = [json.loads(o) for o in self._batch(c, reads, "read authorities")]
        pre: dict[str, dict] = {}
        for i, w in enumerate(authorities):
            bal, nonce, code = vals[3 * i:3 * i + 3]
            pre[w] = {"balance": bal, "nonce": _int(nonce), "code": (code or "0x").lower()}

        # EIP-7702: the sender's nonce is bumped before the authorization list is processed.
        current = {w: pre[w]["nonce"] + (1 if w == tx.from_ else 0) for w in authorities}
        code = {w: pre[w]["code"] for w in authorities}
        applied = {w: 0 for w in authorities}
        for a in auths:
            w = authority_of(a, tx)
            if a.chain_id not in (0, tx.chain_id) or a.nonce != current[w]:
                continue
            if code[w] not in ("0x", "") and not code[w].startswith(DELEGATION_PREFIX):
                continue  # an account with real contract code cannot be an authority
            code[w] = "0x" if a.address == ZERO_ADDRESS else DELEGATION_PREFIX + a.address[2:]
            current[w] += 1
            applied[w] += 1

        synth = {"pre": {}, "post": {}}
        cmds, expect = [], []
        for w in authorities:
            if not applied[w]:
                continue
            n0, new_nonce = pre[w]["nonce"], pre[w]["nonce"] + applied[w]
            cmds += [_rpc("anvil_setCode", [w, code[w]]), _rpc("anvil_setNonce", [w, hex(new_nonce)])]
            expect += [(_rpc("eth_getCode", [w, "latest"]), code[w]),
                       (_rpc("eth_getTransactionCount", [w, "latest"]), hex(new_nonce))]
            p = {"balance": pre[w]["balance"]}
            if n0:
                p["nonce"] = n0
            if pre[w]["code"] != "0x":
                p["code"] = pre[w]["code"]
            synth["pre"][w] = p
            synth["post"][w] = {"code": code[w], "nonce": new_nonce}
        if cmds:
            self._batch(c, cmds, "apply delegation effect")
            self._verify(c, expect, "delegation effect")
        return synth

    def _sign_auths(self, c: Any, tx: TxRequest) -> list[str]:
        cmds = []
        for a in tx.authorization_list or []:
            who = authority_of(a, tx)
            cmds.append([
                "cast", "wallet", "sign-auth", a.address,
                "--nonce", str(a.nonce), "--chain", str(a.chain_id),
                "--mnemonic", ANVIL_MNEMONIC, "--mnemonic-index", str(ANVIL_ACCOUNTS.index(who)),
                "-r", RPC,
            ])
        signed = [s.strip() for s in self._batch(c, cmds, "sign authorizations")]
        for s in signed:
            if not re.fullmatch(r"0x[0-9a-fA-F]+", s):
                raise SandboxError("sign-auth returned no authorization")
        return signed

    def _send(self, c: Any, tx: TxRequest, with_auths: bool, impersonate: bool) -> str:
        signed = self._sign_auths(c, tx) if with_auths else []
        if not impersonate and tx.from_ in ANVIL_ACCOUNTS:
            signer = ["--mnemonic", ANVIL_MNEMONIC, "--mnemonic-index", str(ANVIL_ACCOUNTS.index(tx.from_))]
        else:
            signer = ["--unlocked", "--from", tx.from_]  # anvil --auto-impersonate
        cmd = ["cast", "send", "-r", RPC, *signer, *_ZERO_FEES,
               "--gas-limit", str(TX_GAS_LIMIT), "--value", str(tx.value), "--async"]
        for s in signed:
            cmd += ["--auth", s]
        if signed:
            cmd.append("--force")  # skip cast's interactive 7702 warning
        cmd.append(tx.to)
        if tx.data != "0x":
            cmd.append(tx.data)
        return self._send_cmd(c, cmd, "send tx")

    def _send_cmd(self, c: Any, cmd: list[str], what: str) -> str:
        out = self._exec(c, cmd, what)
        hashes = _HASH.findall(out)
        if not hashes:
            raise SandboxError(f"{what}: no transaction hash")
        return hashes[-1].lower()

    def _collect(self, c: Any, tx_hash: str) -> tuple[Any, Any, Any]:
        cmds = [
            _rpc("eth_getTransactionReceipt", [tx_hash]),
            _rpc("eth_getTransactionByHash", [tx_hash]),
            _rpc("debug_traceTransaction",
                 [tx_hash, {"tracer": "prestateTracer", "tracerConfig": {"diffMode": True}}]),
        ]
        receipt = mined_tx = diff = None
        for _ in range(RECEIPT_TRIES):
            receipt, mined_tx, diff = (json.loads(o) for o in self._batch(c, cmds, "collect"))
            if receipt is not None:
                break
            time.sleep(0.1)
        return receipt, mined_tx, diff

    # ---- post-delegation probe ---------------------------------------------------

    def _probe(self, c: Any, diff: dict) -> list[dict[str, Any]]:
        """Call each newly delegated wallet as an outsider and record any funds that move.
        Stops at the first call that moves funds (that is the evidence)."""
        targets = []
        for addr, post in (diff.get("post") or {}).items():
            code = str(post.get("code", ""))
            if code.startswith(DELEGATION_PREFIX) and "0x" + code[8:] != ZERO_ADDRESS:
                targets.append((addr, "0x" + code[8:]))
        results: list[dict[str, Any]] = []
        for wallet, delegate in targets:
            dcode = json.loads(self._exec(c, _rpc("eth_getCode", [delegate, "latest"]), "read delegate code"))
            embedded = [a for a in push20_addresses(dcode or "0x") if a not in (wallet, delegate, ZERO_ADDRESS)]
            callers = [(a, "address hard-coded in delegate code") for a in embedded[:MAX_EMBEDDED_CALLERS]]
            callers.append(("0x" + secrets.token_hex(20), "random outside account"))
            for caller, kind in callers:
                for data, name in PROBE_CALLS:
                    entry = self._probe_call(c, wallet, delegate, caller, kind, data, name)
                    results.append(entry)
                    if entry["moved"]:
                        return results
        return results

    def _probe_call(self, c: Any, wallet: str, delegate: str, caller: str, kind: str,
                    data: str, name: str) -> dict[str, Any]:
        entry: dict[str, Any] = {"wallet": wallet, "delegate": delegate, "caller": caller,
                                 "caller_kind": kind, "call": name, "selector": data,
                                 "status": None, "native_out": {}, "token_out": [], "moved": False}
        snap = json.loads(self._exec(c, _rpc("evm_snapshot", []), "probe snapshot"))
        try:
            cmd = ["cast", "send", "-r", RPC, "--unlocked", "--from", caller, *_ZERO_FEES,
                   "--gas-limit", str(TX_GAS_LIMIT), "--async", wallet]
            if data != "0x":
                cmd.append(data)
            h = self._send_cmd(c, cmd, "probe call")
            receipt, _, diff = self._collect(c, h)
            if not isinstance(receipt, dict) or not isinstance(diff, dict):
                entry["status"] = "no receipt"
                return entry
            entry["status"] = receipt.get("status")
            pre_all, post_all = diff.get("pre", {}), diff.get("post", {})
            w_pre = _int(pre_all.get(wallet, {}).get("balance", "0x0"))
            w_post = _int(post_all.get(wallet, {}).get("balance", hex(w_pre)))
            if w_post < w_pre:
                for addr, post in post_all.items():
                    if addr == wallet or "balance" not in post:
                        continue
                    gain = _int(post["balance"]) - _int(pre_all.get(addr, {}).get("balance", "0x0"))
                    if gain > 0:
                        entry["native_out"][addr] = str(gain)
            for lg in receipt.get("logs") or []:
                topics = [t.lower() for t in lg.get("topics", [])]
                if len(topics) == 3 and topics[0] == TRANSFER_TOPIC and "0x" + topics[1][-40:] == wallet:
                    entry["token_out"].append({"token": str(lg.get("address", "")).lower(),
                                               "to": "0x" + topics[2][-40:],
                                               "amount": str(_int(lg.get("data") or "0x0"))})
            entry["moved"] = bool(entry["native_out"] or entry["token_out"])
            return entry
        finally:
            self._exec(c, _rpc("evm_revert", [snap]), "probe revert")

    # ---- docker helpers -----------------------------------------------------

    def _exec(self, c: Any, argv: list[str], what: str) -> str:
        res = c.exec_run(argv, demux=True)
        out, err = res.output if isinstance(res.output, tuple) else (res.output, b"")
        if res.exit_code != 0:
            msg = (err or out or b"").decode(errors="replace").strip()[-300:]
            raise SandboxError(f"{what} failed (exit {res.exit_code}): {msg}")
        return (out or b"").decode(errors="replace")

    def _batch(self, c: Any, cmds: list[list[str]], what: str) -> list[str]:
        """Run several commands in one exec; stop at the first failure."""
        if not cmds:
            return []
        script = f" && echo {SEP} && ".join(shlex.join(cmd) for cmd in cmds)
        parts = self._exec(c, ["sh", "-c", script], what).split(SEP)
        if len(parts) != len(cmds):
            raise SandboxError(f"{what}: expected {len(cmds)} outputs, got {len(parts)}")
        return [p.strip() for p in parts]

    def _destroy(self, run: _Run) -> None:
        with run.lock:
            run.cancelled = True
            container = run.container
        if container is not None:
            self._remove(container)

    def _remove(self, container: Any) -> None:
        try:
            container.remove(force=True)
        except Exception as e:  # noqa: BLE001
            if type(e).__name__ == "NotFound":
                return
            log.error("could not remove sandbox container %s: %s", getattr(container, "name", "?"), e)
