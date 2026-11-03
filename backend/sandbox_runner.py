"""Run one signing request in a fresh, locked-down Anvil container.

One container per check: no network, read-only root, tmpfs for temp, memory/CPU/pids
limits, all capabilities dropped, no-new-privileges, no env vars or secrets passed in.
The host drives Anvil with `docker exec` + `cast` (the container has no network, so
there is no port to talk to). The container is always destroyed, also on error/timeout.

Content is validated, exit codes are not trusted: a missing receipt, empty state diff
or mismatched authorizationList raises SandboxError, which decide() turns into R0.

Keys: only the public Anvil test mnemonic, used inside the sandbox, by index.
"""

import json
import logging
import re
import shlex
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any

from app.config import Settings
from app.models import Authorization, SimulationResult, TxRequest
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
_HASH = re.compile(r"0x[0-9a-fA-F]{64}")


class SandboxError(Exception):
    """The sandbox could not produce a trustworthy result. Always means R0 / BLOCK."""


def anvil_args(chain_id: int) -> list[str]:
    return [
        "--hardfork", "prague",
        "--chain-id", str(chain_id),
        "--host", "127.0.0.1",
        "--port", "8545",
        "--auto-impersonate",
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


def _rpc(method: str, params: list[Any]) -> list[str]:
    return ["cast", "rpc", "-r", RPC, "--raw", method, json.dumps(params)]


def _hex32(v: str) -> str:
    return "0x" + v[2:].rjust(64, "0")


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

    def run(self, tx: TxRequest, state: StartingState) -> SimulationResult:
        """Simulate `tx` on `state`. Returns an ok result or raises SandboxError."""
        run = _Run(name=f"fc-sandbox-{uuid.uuid4().hex[:12]}")
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sandbox")
        timeout = self.settings.sandbox_timeout_seconds
        try:
            future = pool.submit(self._run, run, tx, state)
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

    def _run(self, run: _Run, tx: TxRequest, state: StartingState) -> SimulationResult:
        t = self.clock

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
        tx_hash = self._send(container, tx)
        run.timings["tx"] = t() - t0

        t0 = t()
        receipt, mined_tx, diff = self._collect(container, tx_hash)
        run.timings["trace"] = t() - t0

        validate_result(tx, tx_hash, receipt, mined_tx, diff)
        return SimulationResult(
            ok=True, receipt=receipt, state_diff=diff, tx=mined_tx, sandbox=run.limits
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
        if not cmds:
            return
        self._batch(c, cmds, "state inject")

        # Read back: never assume the injection worked.
        got = self._batch(c, [e[0] for e in expect], "state read-back")
        for (cmd, want), out in zip(expect, got, strict=True):
            method, value = cmd[5], json.loads(out)
            if not isinstance(value, str):
                ok = False
            elif method == "eth_getCode":
                ok = value.lower() == want.lower()
            else:
                ok = int(value, 16) == int(want, 16)
            if not ok:
                raise SandboxError(f"state inject mismatch: {method} {cmd[6]}")

    def _sign_auths(self, c: Any, auths: list[Authorization]) -> list[str]:
        cmds = []
        for a in auths:
            if a.signed:
                raise SandboxError("pre-signed authorizations are not supported yet")
            if a.authority is None:
                raise SandboxError("unsigned authorization without authority")
            if a.authority not in ANVIL_ACCOUNTS:
                raise SandboxError(f"cannot simulate an unsigned authorization for {a.authority}: not a sandbox account")
            cmds.append([
                "cast", "wallet", "sign-auth", a.address,
                "--nonce", str(a.nonce), "--chain", str(a.chain_id),
                "--mnemonic", ANVIL_MNEMONIC, "--mnemonic-index", str(ANVIL_ACCOUNTS.index(a.authority)),
                "-r", RPC,
            ])
        signed = [s.strip() for s in self._batch(c, cmds, "sign authorizations")]
        for s in signed:
            if not re.fullmatch(r"0x[0-9a-fA-F]+", s):
                raise SandboxError("sign-auth returned no authorization")
        return signed

    def _send(self, c: Any, tx: TxRequest) -> str:
        auths = tx.authorization_list or []
        signed = self._sign_auths(c, auths) if auths else []
        if tx.from_ in ANVIL_ACCOUNTS:
            signer = ["--mnemonic", ANVIL_MNEMONIC, "--mnemonic-index", str(ANVIL_ACCOUNTS.index(tx.from_))]
        else:
            signer = ["--unlocked", "--from", tx.from_]  # anvil --auto-impersonate
        cmd = ["cast", "send", "-r", RPC, *signer,
               "--gas-limit", str(TX_GAS_LIMIT), "--value", str(tx.value), "--async"]
        for s in signed:
            cmd += ["--auth", s]
        if signed:
            cmd.append("--force")  # skip cast's interactive 7702 warning
        cmd.append(tx.to)
        if tx.data != "0x":
            cmd.append(tx.data)
        out = self._exec(c, cmd, "send tx")
        hashes = _HASH.findall(out)
        if not hashes:
            raise SandboxError("send returned no transaction hash")
        return hashes[-1].lower()

    def _collect(self, c: Any, tx_hash: str) -> tuple[Any, Any, Any]:
        cmds = [
            _rpc("eth_getTransactionReceipt", [tx_hash]),
            _rpc("eth_getTransactionByHash", [tx_hash]),
            _rpc("debug_traceTransaction",
                 [tx_hash, {"tracer": "prestateTracer", "tracerConfig": {"diffMode": True}}]),
        ]
        for _ in range(RECEIPT_TRIES):
            receipt, mined_tx, diff = (json.loads(o) for o in self._batch(c, cmds, "collect"))
            if receipt is not None:
                return receipt, mined_tx, diff
            time.sleep(0.1)
        return None, mined_tx, diff

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
