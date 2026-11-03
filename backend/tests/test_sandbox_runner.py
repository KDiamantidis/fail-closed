import time

import pytest

from app.config import Settings
from app.models import TxRequest
from sandbox_runner import (
    ANVIL_ACCOUNTS,
    SandboxError,
    SandboxRunner,
    container_options,
    delegation_mode,
    merge_diffs,
    push20_addresses,
)
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
    assert set(res.timings) == {"container_start", "state_inject", "tx", "trace", "probe", "destroy"}
    assert res.delegation_mode == "type4"
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
    send = d.chain.sends[0]
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


# --- delegation modes ------------------------------------------------------------

REAL = "0x" + "a7" * 20  # a real wallet: no key in the sandbox


def test_mode_selection():
    assert delegation_mode(TxRequest.model_validate(tx())) is None
    assert delegation_mode(attack_tx()) == "type4"
    assert delegation_mode(attack_tx(authorization_list=[
        {"chain_id": 1, "address": SWEEPER, "nonce": 1, "authority": REAL}])) == "effect"
    assert delegation_mode(attack_tx(authorization_list=[
        {"chain_id": 1, "address": SWEEPER, "nonce": 1, "authority": HOT, "y_parity": 0, "r": "0x1", "s": "0x2"}
    ])) == "effect"  # pre-signed: we never re-sign, we apply the effect


def test_effect_mode_sets_code_and_bumps_nonce_by_eip7702_rules():
    state = StartingState.model_validate({"accounts": {REAL: {"balance": 10**20, "nonce": 7},
                                                       SWEEPER: {"code": "artifact:MockSweeper"}}})
    t = attack_tx(**{"from": ATTACKER}, authorization_list=[
        {"chain_id": 1, "address": SWEEPER, "nonce": n, "authority": REAL} for n in range(3, 8)])
    chain = FakeChain()
    chain.diff = {"pre": {ATTACKER: {"balance": "0x1"}}, "post": {ATTACKER: {"nonce": 1}}}
    d = FakeDocker(chain)
    res = runner(d).run(t, state)
    assert res.delegation_mode == "effect"
    assert chain.code[REAL] == "0xef0100" + SWEEPER[2:]
    assert chain.nonce[REAL] == 8  # only nonce 7 matched
    assert not any(c[:3] == ["cast", "wallet", "sign-auth"] for c in chain.commands)  # no keys used
    send = chain.sends[0]
    assert "--unlocked" in send and "--auth" not in send
    assert res.state_diff["post"][REAL] == {"code": "0xef0100" + SWEEPER[2:], "nonce": 8}
    assert res.state_diff["pre"][REAL] == {"balance": hex(10**20), "nonce": 7}
    assert len(res.tx["authorizationList"]) == 5 and res.tx["authorizationList"][0]["simulated"]


def test_effect_mode_self_sponsored_needs_nonce_plus_one():
    state = StartingState.model_validate({"accounts": {REAL: {"balance": 10**20, "nonce": 6}}})
    t = TxRequest.model_validate(tx(**{"from": REAL, "to": REAL, "value": 0}, authorization_list=[
        {"chain_id": 1, "address": SWEEPER, "nonce": n} for n in range(3, 8)]))
    chain = FakeChain()
    d = FakeDocker(chain)
    runner(d).run(t, state)
    # the tx itself uses nonce 6, so only the authorization with nonce 7 applies;
    # the runner sets 7 and the tx bumps it to 8
    assert chain.nonce[REAL] == 7 and chain.code[REAL].startswith("0xef0100")


def test_effect_mode_pre_signed_without_authority_raises():
    t = attack_tx(authorization_list=[{"chain_id": 1, "address": SWEEPER, "nonce": 1,
                                       "y_parity": 0, "r": "0x1", "s": "0x2"}])
    d = FakeDocker()
    with pytest.raises(SandboxError, match="authority of a pre-signed"):
        runner(d).run(t, STATE)
    assert removed(d)


def test_merge_diffs_matches_a_real_type4_diff():
    # real type-4 (from the Phase 2 probe): nonce 6 -> 8, code set, balance unchanged
    real = {"pre": {HOT: {"balance": "0x10", "nonce": 6}},
            "post": {HOT: {"code": "0xef0100" + SWEEPER[2:], "nonce": 8}}}
    synthetic = {"pre": {HOT: {"balance": "0x10", "nonce": 6}},
                 "post": {HOT: {"code": "0xef0100" + SWEEPER[2:], "nonce": 7}}}
    tx_part = {"pre": {HOT: {"balance": "0x10", "nonce": 7, "code": "0xef0100" + SWEEPER[2:]}},
               "post": {HOT: {"nonce": 8}}}
    assert merge_diffs(synthetic, tx_part) == real


def test_merge_diffs_zero_nonce_and_empty_code_stay_omitted_in_pre():
    synthetic = {"pre": {HOT: {"balance": "0x0"}}, "post": {HOT: {"code": "0xef01", "nonce": 1}}}
    tx_part = {"pre": {HOT: {"balance": "0x0", "nonce": 1, "code": "0xef01"}}, "post": {HOT: {"nonce": 2}}}
    assert merge_diffs(synthetic, tx_part) == {"pre": {HOT: {"balance": "0x0"}},
                                               "post": {HOT: {"code": "0xef01", "nonce": 2}}}


# --- probe after delegation ----------------------------------------------------------

SWEEPER_CODE = "0x6080" + "73" + ATTACKER[2:] + "33" + "73" + ATTACKER[2:] + "60ff"


def test_push20_addresses():
    assert push20_addresses(SWEEPER_CODE) == [ATTACKER]
    assert push20_addresses("0x7f" + "73" * 32) == []  # 0x73 inside PUSH32 data is not an opcode
    assert push20_addresses("0xzz") == []


def delegated_chain() -> FakeChain:
    chain = FakeChain()
    chain.artifact_code = SWEEPER_CODE
    chain.diff = {"pre": {HOT: {"balance": hex(100 * 10**18), "nonce": 7}},
                  "post": {HOT: {"code": "0xef0100" + SWEEPER[2:], "nonce": 8}}}
    return chain


def test_probe_finds_sweep_by_embedded_attacker_and_stops():
    chain = delegated_chain()
    chain.probe_results["0x35faa416"] = (
        {"status": "0x1", "logs": []},
        {"pre": {HOT: {"balance": hex(100 * 10**18)}, ATTACKER: {"balance": "0x0"}},
         "post": {HOT: {"balance": "0x0"}, ATTACKER: {"balance": hex(100 * 10**18)}}})
    res = runner(FakeDocker(chain)).run(attack_tx(data="0x"), STATE)
    calls = [(p["caller"], p["call"], p["moved"]) for p in res.probe]
    assert calls == [(ATTACKER, "empty call", False), (ATTACKER, "sweep()", True)]
    assert res.probe[-1]["native_out"] == {ATTACKER: str(100 * 10**18)}
    assert res.probe[-1]["caller_kind"] == "address hard-coded in delegate code"
    assert chain.snapshots == chain.reverts == 2  # every probe call is rolled back


def test_probe_tries_random_outsider_and_records_nothing_moved():
    chain = delegated_chain()
    chain.artifact_code = "0x6080"  # no embedded addresses
    res = runner(FakeDocker(chain)).run(attack_tx(data="0x"), STATE)
    assert len(res.probe) == 4 and not any(p["moved"] for p in res.probe)
    assert {p["caller_kind"] for p in res.probe} == {"random outside account"}


def test_no_probe_without_delegation():
    res = runner(FakeDocker()).run(TxRequest.model_validate(tx(**{"from": ANVIL_ACCOUNTS[0]})), STATE)
    assert res.probe == [] and res.delegation_mode is None


# --- fees and token balances ----------------------------------------------------------

def test_zero_fees_everywhere():
    d = FakeDocker()
    runner(d).run(attack_tx(), STATE)
    args = d.containers.run_kwargs["command"]
    assert args[args.index("--base-fee") + 1] == "0" and args[args.index("--gas-price") + 1] == "0"
    send = d.chain.sends[0]
    assert send[send.index("--gas-price") + 1] == "0" and send[send.index("--priority-gas-price") + 1] == "0"


def test_token_balances_injected_into_balanceof_slot_and_verified():
    token = "0x" + "70" * 20
    state = StartingState.model_validate({"accounts": {
        HOT: {"balance": 1, "token_balances": {token: 5 * 10**18}}, token: {"code": "artifact:MockToken"}}})
    chain = FakeChain()
    runner(FakeDocker(chain)).run(TxRequest.model_validate(tx(**{"from": HOT})), state)
    slot = "0x" + (HOT[2:] + "00" * 12)[:64]
    assert int(chain.storage[(token, slot)], 16) == 5 * 10**18


def test_plain_transfer_without_auths():
    d = FakeDocker()
    res = runner(d).run(TxRequest.model_validate(tx(**{"from": ANVIL_ACCOUNTS[0]})), STATE)
    assert res.ok
    send = next(c for c in d.chain.commands if c[:2] == ["cast", "send"])
    assert "--auth" not in send and "--force" not in send
