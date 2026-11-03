"""A tiny fake of the Docker SDK + an in-memory 'anvil' so the runner can be unit-tested."""

import copy
import json
import shlex
import threading
import time
from dataclasses import dataclass

from sandbox_runner import SEP

TX_HASH = "0x" + "ab" * 32


@dataclass
class ExecResult:
    exit_code: int
    output: tuple[bytes, bytes]


class FakeChain:
    """Answers the cast commands the runner sends. Override attributes to inject faults."""

    def __init__(self):
        self.balance: dict[str, int] = {}
        self.nonce: dict[str, int] = {}
        self.code: dict[str, str] = {}
        self.storage: dict[tuple[str, str], str] = {}
        self.auth_count = 0
        self.sends: list[list[str]] = []
        self.receipt = {"transactionHash": TX_HASH, "status": "0x1", "logs": []}
        self.diff = {"pre": {"0xaa": {"balance": "0x10"}}, "post": {"0xaa": {"balance": "0x0"}}}
        self.artifact_code = "0x6080604052"
        self.probe_results: dict[str, tuple[dict, dict]] = {}  # selector -> (receipt, diff)
        self.mined_tx_override = None
        self.drop_set_nonce = False
        self.block_on: str | None = None  # command word that hangs until the container is removed
        self.fail_on: str | None = None  # command word that exits non-zero
        self.kill_on: str | None = None  # command word at which the container is killed from outside
        self.trace_raw: str | None = None  # raw (possibly broken) output for debug_traceTransaction
        self.commands: list[list[str]] = []
        self.snapshots = 0
        self.reverts = 0
        self.block = 0
        self.chain_id = 1
        self.revert_noop = False  # simulate a revert that silently leaves state behind
        self._saved: dict[str, tuple] = {}

    def mined_tx(self):
        if self.mined_tx_override is not None:
            return self.mined_tx_override
        return {"hash": TX_HASH, "authorizationList": [{"nonce": hex(i)} for i in range(self.auth_count)]}

    def _probe(self, h: str):
        idx = int(h, 16) - 1
        cmd = self.sends[idx + 1]
        selector = cmd[-1] if cmd[-1].startswith("0x") and len(cmd[-1]) == 10 else "0x"
        receipt, diff = self.probe_results.get(selector, ({"status": "0x0", "logs": []}, {"pre": {}, "post": {}}))
        return {"transactionHash": h, **receipt}, diff

    def handle(self, argv: list[str], removed: threading.Event) -> tuple[int, str]:
        self.commands.append(argv)
        key = " ".join(argv[:3]) if argv[0] == "cast" else argv[0]
        method = argv[5] if argv[:2] == ["cast", "rpc"] else None
        word = method or key
        if self.block_on and self.block_on in word:
            removed.wait(30)
            return 137, "killed"
        if self.fail_on and self.fail_on in word:
            return 1, "boom"
        if self.kill_on and self.kill_on in word:
            removed.set()  # someone killed the container in the middle of the check
            return 137, "container killed"
        if self.trace_raw is not None and method == "debug_traceTransaction":
            return 0, self.trace_raw
        if argv[:2] == ["cast", "block-number"]:
            return 0, "1"
        if argv[0] == "cat":
            return 0, self.artifact_code + "\n"
        if argv[:3] == ["cast", "index", "address"]:
            return 0, "0x" + (argv[3][2:] + "00" * 12)[:64]
        if argv[:3] == ["cast", "wallet", "sign-auth"]:
            self.auth_count += 1
            return 0, "0xf85a" + "00" * 40
        if argv[:2] == ["cast", "send"]:
            self.sends.append(argv)
            main = self.block == 0  # the request's own tx is the first block after a clean state
            self.block += 1
            return 0, TX_HASH if main else "0x" + format(len(self.sends) - 1, "064x")
        if method:
            p = json.loads(argv[6])
            if method == "anvil_setBalance":
                self.balance[p[0]] = int(p[1], 16)
            elif method == "anvil_setNonce":
                if not self.drop_set_nonce:
                    self.nonce[p[0]] = int(p[1], 16)
            elif method == "anvil_setCode":
                self.code[p[0]] = p[1]
            elif method == "anvil_setStorageAt":
                self.storage[(p[0], p[1])] = p[2]
            elif method == "eth_getBalance":
                return 0, json.dumps(hex(self.balance.get(p[0], 0)))
            elif method == "eth_getTransactionCount":
                return 0, json.dumps(hex(self.nonce.get(p[0], 0)))
            elif method == "eth_getCode":
                return 0, json.dumps(self.code.get(p[0], "0x"))
            elif method == "eth_getStorageAt":
                return 0, json.dumps(self.storage.get((p[0], p[1]), "0x" + "0" * 64))
            elif method == "evm_snapshot":
                self.snapshots += 1
                sid = hex(self.snapshots)
                self._saved[sid] = copy.deepcopy((self.balance, self.nonce, self.code, self.storage, self.block))
                return 0, json.dumps(sid)
            elif method == "evm_revert":
                self.reverts += 1
                saved = self._saved.pop(p[0], None)
                if saved is None:
                    return 0, "false"
                if not self.revert_noop:
                    self.balance, self.nonce, self.code, self.storage, self.block = saved
                return 0, "true"
            elif method == "eth_blockNumber":
                return 0, json.dumps(hex(self.block))
            elif method == "anvil_setChainId":
                self.chain_id = int(p[0])
            elif method == "eth_getTransactionReceipt":
                if p[0] == TX_HASH:
                    return 0, json.dumps(self.receipt)
                return 0, json.dumps(self._probe(p[0])[0])
            elif method == "eth_getTransactionByHash":
                return 0, json.dumps(self.mined_tx() if p[0] == TX_HASH else {"hash": p[0]})
            elif method == "debug_traceTransaction":
                return 0, json.dumps(self.diff if p[0] == TX_HASH else self._probe(p[0])[1])
            return 0, "null"
        return 127, f"unknown command {argv}"


class FakeContainer:
    def __init__(self, chain: FakeChain, name: str):
        self.chain = chain
        self.name = name
        self.removed = threading.Event()
        self.attrs = {
            "HostConfig": {"NetworkMode": "none", "ReadonlyRootfs": True, "Memory": 536870912,
                           "NanoCpus": 1_000_000_000, "PidsLimit": 128, "CapDrop": ["ALL"],
                           "SecurityOpt": ["no-new-privileges"]},
            "Config": {"Env": ["PATH=/usr/local/bin:/usr/bin"], "User": "foundry"},
        }

    def reload(self):
        pass

    def exec_run(self, argv, demux=True):
        if self.removed.is_set():
            return ExecResult(137, (b"", b"container removed"))
        if argv[:2] == ["sh", "-c"]:
            outs = []
            for part in argv[2].split(f" && echo {SEP} && "):
                code, out = self.chain.handle(shlex.split(part), self.removed)
                if code != 0:
                    return ExecResult(code, (b"", out.encode()))
                outs.append(out)
            return ExecResult(0, (f"\n{SEP}\n".join(outs).encode(), b""))
        code, out = self.chain.handle(list(argv), self.removed)
        return ExecResult(code, (out.encode(), b"") if code == 0 else (b"", out.encode()))

    def remove(self, force=False):
        self.removed.set()


class FakeContainers:
    def __init__(self, chain: FakeChain, run_delay: float = 0.0, run_error: Exception | None = None,
                 chain_factory=None):
        self.chain = chain
        self.chain_factory = chain_factory  # one fresh chain per container (warm-pool tests)
        self.chains: list[FakeChain] = []
        self.run_delay = run_delay
        self.run_error = run_error
        self.created: list[FakeContainer] = []
        self.run_kwargs: dict | None = None

    def run(self, image, command=None, name=None, **kwargs):
        self.run_kwargs = {"image": image, "command": command, "name": name, **kwargs}
        if self.run_error:
            raise self.run_error
        if self.run_delay:
            time.sleep(self.run_delay)
        chain = self.chain_factory() if self.chain_factory else self.chain
        self.chains.append(chain)
        c = FakeContainer(chain, name)
        self.created.append(c)
        return c


class FakeDocker:
    def __init__(self, chain: FakeChain | None = None, **kw):
        self.chain = chain or FakeChain()
        self.containers = FakeContainers(self.chain, **kw)
