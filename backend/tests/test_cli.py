from pathlib import Path

import pytest

from cli import auth_outcomes, field_change, main

SCENARIO = Path(__file__).resolve().parents[2] / "scenarios" / "7702_attack"


def test_field_change_one_sided_means_zero():
    # diffMode: empty code / zero nonce are missing from "pre"
    assert field_change({}, {"code": "0xef0100ab"}, "code") == ("0x", "0xef0100ab")
    assert field_change({"balance": "0x10"}, {}, "balance") == ("0x10", "0x0")
    assert field_change({}, {}, "nonce") is None


def test_auth_outcomes_only_matching_nonce_applies():
    auths = [{"chainId": "0x1", "nonce": hex(n)} for n in range(3, 8)]
    out = auth_outcomes(auths, start_nonce=7, chain_id=1)
    assert [o.startswith("APPLIED") for o in out] == [False] * 4 + [True]


def test_auth_outcomes_consecutive_all_apply_from_current():
    auths = [{"chainId": "0x0", "nonce": hex(n)} for n in (7, 8, 9)]
    assert all(o.startswith("APPLIED") for o in auth_outcomes(auths, 7, 1))


def test_auth_outcomes_wrong_chain_skipped():
    assert auth_outcomes([{"chainId": "0x38", "nonce": "0x7"}], 7, 1) == ["skipped (wrong chain)"]


@pytest.mark.docker
def test_7702_scenario_end_to_end(capsys):
    assert main(["scenario", str(SCENARIO)]) == 0
    out = capsys.readouterr().out
    assert "code    : 0x -> 0xef010000000000000000000000000000000000005eeeee" in out
    assert "nonce   : 7 -> 8" in out
    assert "100.000000 ETH -> 0.000000 ETH" in out
    assert "MOCK - no model was called" in out
    assert "NetworkMode    : none" in out
