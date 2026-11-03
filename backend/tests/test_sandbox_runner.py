import time

import pytest

from app.config import Settings
from app.models import TxRequest
from sandbox_runner import ANVIL_ACCOUNTS, SandboxError, SandboxRunner, container_options
from state_provider import StartingState
from tests.fake_docker import FakeChain, FakeDocker
from tests.helpers import BOB, tx

HOT = ANVIL_ACCOUNTS[1]
ATTACKER = ANVIL_ACCOUNTS[2]
SWEEPER = "0x00000000000000000000000000000000005eeeee"
SECRET = "sk-THIS-MUST-NEVER-REACH-THE-SANDBOX"

STATE = StartingState.model_validate({"accounts": {
    HOT: {"balance": 100 * 10**18, "nonce": 7},
    ATTACKER: {"balance": 10**18},
    SWEEPER: {"code": "artifact:MockSweeper", "storage": {"0x0": "0x1"}},
}})


def settings(**kw) -> Settings:
    return Settings(vultr_inference_api_key=SECRET, sandbox_timeout_seconds=kw.pop("timeout", 5), **kw)


def attack_tx(**kw) -> TxRequest:
    return TxRequest.model_validate(tx(**{
        "from": ATTACKER, "to": HOT, "data": "0xa55a1503", "value": 0,
        "authorization_list": [
            {"chain_id": 1, "address": SWEEPER, "nonce": n, "authority": HOT} for n in range(3, 8)
        ],
        **kw,
    }))


def runner(docker: FakeDocker, **kw) -> SandboxRunner:
    return SandboxRunner(settings(**kw), client=docker)


def removed(docker: FakeDocker) -> bool:
    return all(c.removed.is_set() for c in docker.containers.created)


# --- happy path ---------------------------------------------------------------

def test_happy_path_returns_joined_result_and_destroys_container():
    d = FakeDocker()
    res = runner(d).run(attack_tx(), STATE)
    assert res.ok is True
    assert res.receipt["status"] == "0x1"
    assert res.state_diff["post"]
    assert len(res.tx["authorizationList"]) == 5
    assert set(res.timings) == {"container_start", "state_inject", "tx", "trace", "destroy"}
    assert res.sandbox["NetworkMode"] == "none"
    assert len(d.containers.created) == 1 and removed(d)


def test_state_is_injected_and_auths_signed_by_authority_index():
    d = FakeDocker()
    runner(d).run(attack_tx(), STATE)
    assert d.chain.balance[HOT] == 100 * 10**18 and d.chain.nonce[HOT] == 7
    assert d.chain.code[SWEEPER] == "0x6080604052"  # resolved from the image artifact
    signs = [c for c in d.chain.commands if c[:3] == ["cast", "wallet", "sign-auth"]]
    assert len(signs) == 5
    assert all(c[c.index("--mnemonic-index") + 1] == "1" for c in signs)  # HOT = index 1
    send = next(c for c in d.chain.commands if c[:2] == ["cast", "send"])
    assert send.count("--auth") == 5 and "--force" in send
    assert send[send.index("--mnemonic-index") + 1] == "2"  # ATTACKER = index 2


# --- container hardening ------------------------------------------------------

def test_container_options_are_locked_down():
    o = container_options(settings())
    assert o["network_mode"] == "none"
    assert o["read_only"] is True
    assert o["mem_limit"] == "512m" and o["memswap_limit"] == "512m"
    assert o["nano_cpus"] == 1_000_000_000
    assert o["pids_limit"] == 128
    assert o["cap_drop"] == ["ALL"]
    assert o["security_opt"] == ["no-new-privileges"]
    assert o["privileged"] is False
    assert o["environment"] == {}
    assert set(o["tmpfs"]) == {"/tmp", "/home/foundry"}
    for forbidden in ("volumes", "mounts", "ports", "devices", "cap_add", "network"):
        assert forbidden not in o


def test_no_secret_reaches_the_container():
    d = FakeDocker()
    runner(d).run(attack_tx(), STATE)
    assert SECRET not in repr(d.containers.run_kwargs)
    assert all(SECRET not in " ".join(c) for c in d.chain.commands)


# --- content validation: fail closed -----------------------------------------

def test_empty_diff_raises_and_destroys():
    chain = FakeChain()
    chain.diff = {"pre": {}, "post": {}}
    d = FakeDocker(chain)
    with pytest.raises(SandboxError, match="empty state diff"):
        runner(d).run(attack_tx(), STATE)
    assert removed(d)


def test_null_diff_raises():
    chain = FakeChain()
    chain.diff = None
    with pytest.raises(SandboxError, match="empty state diff"):
        runner(FakeDocker(chain)).run(attack_tx(), STATE)


def test_missing_receipt_raises_and_destroys(monkeypatch):
    monkeypatch.setattr("sandbox_runner.RECEIPT_TRIES", 2)
    chain = FakeChain()
    chain.receipt = None
    d = FakeDocker(chain)
    with pytest.raises(SandboxError, match="missing receipt"):
        runner(d).run(attack_tx(), STATE)
    assert removed(d)


def test_receipt_for_other_tx_raises():
    chain = FakeChain()
    chain.receipt = {"transactionHash": "0x" + "cd" * 32, "status": "0x1"}
    with pytest.raises(SandboxError, match="different transaction"):
        runner(FakeDocker(chain)).run(attack_tx(), STATE)


def test_authorization_list_mismatch_raises():
    chain = FakeChain()
    chain.mined_tx_override = {"hash": "0x" + "ab" * 32, "authorizationList": []}
    with pytest.raises(SandboxError, match="authorizationList mismatch"):
        runner(FakeDocker(chain)).run(attack_tx(), STATE)


def test_state_inject_is_verified():
    chain = FakeChain()
    chain.drop_set_nonce = True  # anvil "accepted" the call but the nonce did not change
    d = FakeDocker(chain)
    with pytest.raises(SandboxError, match="state inject mismatch: eth_getTransactionCount"):
        runner(d).run(attack_tx(), STATE)
    assert removed(d)


# --- errors and timeouts: always destroyed -----------------------------------

def test_exec_error_raises_and_destroys():
    chain = FakeChain()
    chain.fail_on = "cast send"
    d = FakeDocker(chain)
    with pytest.raises(SandboxError, match="send tx failed"):
        runner(d).run(attack_tx(), STATE)
    assert removed(d)


def test_timeout_raises_and_destroys_quickly():
    chain = FakeChain()
    chain.block_on = "cast send"
    d = FakeDocker(chain)
    t0 = time.monotonic()
    with pytest.raises(SandboxError, match="timeout after 0.5s"):
        runner(d, timeout=0.5).run(attack_tx(), STATE)
    assert time.monotonic() - t0 < 3
    assert removed(d)


def test_container_created_after_timeout_is_still_destroyed():
    d = FakeDocker(run_delay=0.6)
    with pytest.raises(SandboxError, match="timeout"):
        runner(d, timeout=0.2).run(attack_tx(), STATE)
    deadline = time.monotonic() + 3
    while not d.containers.created and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(0.1)
    assert d.containers.created and removed(d)


def test_docker_error_becomes_sandbox_error():
    d = FakeDocker(run_error=RuntimeError("image not found"))
    with pytest.raises(SandboxError, match="image not found"):
        runner(d).run(attack_tx(), STATE)


# --- authorizations we cannot simulate honestly ------------------------------

def test_unsigned_auth_for_non_sandbox_account_raises():
    t = attack_tx(authorization_list=[{"chain_id": 1, "address": SWEEPER, "nonce": 1, "authority": BOB}])
    d = FakeDocker()
    with pytest.raises(SandboxError, match="not a sandbox account"):
        runner(d).run(t, STATE)
    assert removed(d)


def test_pre_signed_auth_not_supported_yet():
    t = attack_tx(authorization_list=[{"chain_id": 1, "address": SWEEPER, "nonce": 1,
                                       "y_parity": 0, "r": "0x1", "s": "0x2"}])
    with pytest.raises(SandboxError, match="pre-signed"):
        runner(FakeDocker()).run(t, STATE)


def test_plain_transfer_without_auths():
    d = FakeDocker()
    res = runner(d).run(TxRequest.model_validate(tx(**{"from": ANVIL_ACCOUNTS[0]})), STATE)
    assert res.ok
    send = next(c for c in d.chain.commands if c[:2] == ["cast", "send"])
    assert "--auth" not in send and "--force" not in send
