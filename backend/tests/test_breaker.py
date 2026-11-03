from app.models import Decision, Effect, Finding, TxRequest, Verdict
from breaker import Breaker
from history import History

HOT = "0x" + "a1" * 20
RELAYER = "0x" + "be" * 20
ATT = "0x" + "ba" * 20
SWEEPER = "0x" + "5e" * 20


def tx(**kw):
    base = {"wallet_type": "customer_hot", "chain_id": 1, "from": HOT, "to": ATT, "value": 1}
    return TxRequest.model_validate({**base, **kw})


def verdict(*findings, decision=Decision.BLOCK):
    return Verdict(verdict=decision, reasons=["x"], findings=list(findings), trace_id="t")


def f(rule, action=Decision.BLOCK, effect=None, subject=None):
    return Finding(rule_id=rule, action=action, title=f"{rule} title", effect=effect, subject=subject)


def test_level1_freeze_from_r7_or_r8():
    h = History()
    b = Breaker(h)
    actions = b.apply(tx(), verdict(f("R8", effect=Effect.FREEZE_DESTINATION, subject=ATT)), 1.0, "c1")
    assert actions == [f"level 1: froze destination {ATT} (R8)"]
    assert h.is_frozen(ATT)["check_id"] == "c1"
    # latched: a second trigger changes nothing
    assert b.apply(tx(), verdict(f("R7", effect=Effect.FREEZE_DESTINATION, subject=ATT)), 2.0, "c2") == []


def test_level2_pause_on_r1_r2_r3_block_including_authorities():
    h = History()
    t = tx(**{"from": RELAYER}, to=HOT, authorization_list=[{"chain_id": 1, "address": SWEEPER, "nonce": 1,
                                                              "authority": HOT}])
    actions = Breaker(h).apply(t, verdict(f("R1"), f("R2"), f("C1", Decision.NEEDS_HUMAN)), 1.0, "c1")
    assert {p["wallet"] for p in h.paused()} == {RELAYER, HOT}
    assert all("R1+R2" in a for a in actions)


def test_request_level_r3_needs_human_never_pauses():
    h = History()
    v = verdict(f("R3", Decision.NEEDS_HUMAN, subject=ATT), decision=Decision.NEEDS_HUMAN)
    assert Breaker(h).apply(tx(), v, 1.0, "c1") == [] and h.paused() == []


def test_other_blocks_do_not_pause():
    h = History()
    Breaker(h).apply(tx(), verdict(f("R5"), f("R6"), f("F1")), 1.0, "c1")
    assert h.paused() == [] and h.frozen() == []


def test_nothing_happens_without_a_block_verdict():
    h = History()
    v = verdict(f("R7", Decision.DELAY), decision=Decision.DELAY)
    assert Breaker(h).apply(tx(), v, 1.0, "c1") == []


def test_reset_is_logged_with_actor():
    h = History()
    b = Breaker(h)
    b.apply(tx(), verdict(f("R3", subject=ATT)), 1.0, "c1")
    b.apply(tx(), verdict(f("R8", effect=Effect.FREEZE_DESTINATION, subject=ATT)), 1.0, "c2")
    assert b.reset_wallet(HOT.upper().replace("0X", "0x"), actor="ops-alice", now=5.0) is True
    assert b.reset_destination(ATT, actor="ops-alice", now=6.0) is True
    assert h.paused() == [] and h.frozen() == []
    resets = [e for e in h.events() if e["action"].startswith("reset")]
    assert [(e["action"], e["actor"]) for e in resets] == [("reset_wallet", "ops-alice"),
                                                         ("reset_destination", "ops-alice")]
