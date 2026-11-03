"""Load demo: workload shape, measurement, and the API stream (fake sandbox)."""

import json
from collections import Counter

import pytest

from app.config import Settings
from loadtest import CONFIGS, HOT, build_workload, markdown_table, run_config
from tests.api_helpers import SCENARIOS, FakeRunner, client

BASE = Settings(mock_llm_latency_ms=0, mock_llm_escalate_rate=0.05)


def test_workload_is_realistic_and_reproducible():
    items, state, policy = build_workload(500, seed=7)
    again, _, _ = build_workload(500, seed=7)
    assert [i.tx for i in items] == [i.tx for i in again]
    assert len(items) == 501 and sum(i.attack for i in items) == 1
    normal = [i for i in items if not i.attack]
    kinds = Counter(i.kind for i in normal)
    assert 0.45 < kinds["native"] / 500 < 0.65 and len(kinds) == 4  # native + 3 tokens
    assert {i.tx.from_ for i in normal} == set(HOT)
    assert len({i.tx.chain_id for i in normal}) == 4
    dests = Counter((i.tx.to if i.kind == "native" else "0x" + i.tx.data[34:74]) for i in normal)
    assert len(dests) > 300  # many different destinations
    # a returning customer always uses one chain (they are customers, not a multi-chain drain)
    chains_per_dest: dict[str, set] = {}
    for i in normal:
        d = i.tx.to if i.kind == "native" else "0x" + i.tx.data[34:74]
        chains_per_dest.setdefault(d, set()).add(i.tx.chain_id)
    assert max(len(c) for c in chains_per_dest.values()) == 1
    assert 0 < [i.index for i in items if i.attack][0] < 500  # hidden in the middle
    assert policy["known_tokens"] and state.accounts


def test_run_config_measures_and_stops_the_attack():
    r = run_config("fresh/escalation-on", CONFIGS["fresh/escalation-on"], BASE, n_normal=60,
                   runner=FakeRunner())
    assert r["attacks_stopped"] == 1 and r["attack_result"]["verdict"] == "BLOCK"
    assert r["normal_not_allowed"] == 0, r["normal_not_allowed_by_rule"]
    assert r["normal_signed"] == 60
    assert r["latency_ms"]["fast"]["n"] > 50 and r["latency_ms"]["fast"]["p99"] is not None
    assert r["escalated"] >= 1 and r["latency_ms"]["slow"]["n"] == r["escalated"] + 1
    assert r["llm"]["mode"] == "mock"


def test_escalation_off_means_no_llm_call_on_the_fast_path():
    r = run_config("fresh/escalation-off", CONFIGS["fresh/escalation-off"], BASE, n_normal=60,
                   runner=FakeRunner())
    assert r["escalated"] == 0 and r["latency_ms"]["slow"]["n"] == 1  # only the attack
    assert r["attacks_stopped"] == 1 and r["normal_not_allowed"] == 0


def test_markdown_table_says_mock_llm():
    r = run_config("fresh/escalation-on", CONFIGS["fresh/escalation-on"], BASE, n_normal=20, runner=FakeRunner())
    md = markdown_table([r], {"os": "test", "cpus": 4, "docker": "x"})
    assert "Mock LLM" in md and "| fresh/escalation-on | 1/1 | 0 |" in md


@pytest.mark.skipif(not SCENARIOS.exists(), reason="scenarios/ not available")
def test_api_load_demo_streams_one_event_per_request(tmp_path):
    c, svc = client(tmp_path, runner=FakeRunner())
    svc.loadtest_runner = FakeRunner()
    with c.stream("POST", "/loadtest/run?normal=20") as r:
        body = "".join(r.iter_text())
    data = [json.loads(ln[6:]) for ln in body.splitlines() if ln.startswith("data: ")]
    assert data[0]["llm"] == "mock" and data[0]["normal"] == 20
    rows, summary = data[1:-1], data[-1]
    assert len(rows) == 21 and sum(r["attack"] for r in rows) == 1
    assert summary["attacks_stopped"] == 1 and summary["normal_not_allowed"] == 0
    assert c.get("/history").json() == []  # isolated: the main state is untouched
