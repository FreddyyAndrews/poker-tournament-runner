"""Tests for configurable blinds/antes, the no-decision fixes and new state fields."""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.game import PokerEngine, STARTING_STACK, BIG_BLIND, SMALL_BLIND


def make_engine(stacks, dealer_seat=0, **kwargs):
    ids = [f"bot_{i}" for i in range(len(stacks))]
    return PokerEngine(
        "test_hand",
        ids,
        dealer_seat=dealer_seat,
        starting_stacks=dict(zip(ids, stacks)),
        **kwargs,
    )


def play_out(eng, state, policy):
    """Drive a hand to completion. Returns (result, list of states that asked for action)."""
    asked = []
    for _ in range(500):
        if state["type"] == "hand_complete":
            return state, asked
        asked.append(state)
        state = eng.apply_action(state["seat_to_act"], policy(state))
    raise AssertionError("hand did not terminate")


def call_or_check(state):
    return {"action": "check"} if state["can_check"] else {"action": "call"}


# ---------------------------------------------------------------------------
# Configurable blinds and antes
# ---------------------------------------------------------------------------

def test_defaults_unchanged():
    eng = make_engine([STARTING_STACK] * 3)
    state = eng.start_hand()
    assert state["pot"] == SMALL_BLIND + BIG_BLIND
    assert state["blinds"] == {"sb": SMALL_BLIND, "bb": BIG_BLIND, "ante": 0}
    assert state["min_raise_to"] == 2 * BIG_BLIND


def test_custom_blinds_posted_and_min_raise():
    eng = make_engine([5000] * 3, small_blind=200, big_blind=400)
    state = eng.start_hand()
    assert eng.players[1].bet_this_street == 200
    assert eng.players[2].bet_this_street == 400
    assert state["pot"] == 600
    assert state["amount_owed"] == 400
    assert state["min_raise_to"] == 800


def test_min_raise_resets_to_custom_bb_postflop():
    eng = make_engine([5000] * 2, small_blind=150, big_blind=300)
    state = eng.start_hand()
    state = eng.apply_action(state["seat_to_act"], {"action": "call"})
    state = eng.apply_action(state["seat_to_act"], {"action": "check"})
    assert state["street"] == "flop"
    assert state["min_raise_to"] == 300


def test_antes_are_dead_money():
    eng = make_engine([5000] * 4, small_blind=50, big_blind=100, ante=10)
    state = eng.start_hand()
    assert state["pot"] == 4 * 10 + 50 + 100
    # UTG owes exactly the big blind: the ante does not count toward calling
    assert state["amount_owed"] == 100
    assert all(p.total_invested >= 10 for p in eng.players)
    antes = [a for a in state["action_log"] if a["action"] == "ante"]
    assert len(antes) == 4


def test_ante_can_put_player_all_in():
    eng = make_engine([5000, 5000, 5000, 8], small_blind=50, big_blind=100, ante=10)
    state = eng.start_hand()
    assert eng.players[3].is_all_in
    assert eng.players[3].total_invested == 8
    result, asked = play_out(eng, state, call_or_check)
    assert all(s["seat_to_act"] != 3 for s in asked)
    assert sum(result["final_stacks"].values()) == 15008


def test_invalid_blinds_rejected():
    for kwargs in ({"small_blind": 0, "big_blind": 100},
                   {"small_blind": 200, "big_blind": 100},
                   {"ante": -1}):
        try:
            make_engine([1000, 1000], **kwargs)
        except AssertionError:
            continue
        raise AssertionError(f"expected rejection for {kwargs}")


# ---------------------------------------------------------------------------
# Never ask for a decision that cannot matter
# ---------------------------------------------------------------------------

def test_heads_up_sb_all_in_from_blind_not_asked():
    # Dealer (seat 0) is SB heads-up and only has 30 chips.
    eng = make_engine([30, 5000], small_blind=50, big_blind=100)
    state = eng.start_hand()
    # SB is all-in; BB already covers it, so nobody has a decision.
    assert state["type"] == "hand_complete"
    assert len(state["community_cards"]) == 5
    assert sum(state["final_stacks"].values()) == 5030


def test_heads_up_bb_short_all_in_sb_not_asked():
    # BB (seat 1) can only post 30; SB has already put in more than that.
    eng = make_engine([5000, 30], small_blind=50, big_blind=100)
    state = eng.start_hand()
    assert state["type"] == "hand_complete"
    # BB can win at most 30 from SB (their own 30 + 30 matched).
    assert state["final_stacks"]["bot_1"] in (0, 30, 60)
    assert sum(state["final_stacks"].values()) == 5030


def test_heads_up_bb_short_but_more_than_sb_asks_sb():
    # BB posts 80 all-in (more than the 50 SB) -> SB owes 30 and must decide.
    eng = make_engine([5000, 80], small_blind=50, big_blind=100)
    state = eng.start_hand()
    assert state["type"] == "action_request"
    assert state["seat_to_act"] == 0
    assert state["amount_owed"] == 30
    result, asked = play_out(eng, state, call_or_check)
    assert len(asked) == 1  # nothing to decide after the call
    assert sum(result["final_stacks"].values()) == 5080


def test_multiway_both_blinds_all_in_button_decides_once():
    # dealer=0, SB=1 (20 chips), BB=2 (60 chips): both all-in from blinds.
    eng = make_engine([5000, 20, 60], small_blind=50, big_blind=100)
    state = eng.start_hand()
    assert state["seat_to_act"] == 0
    assert state["amount_owed"] == 60
    result, asked = play_out(eng, state, call_or_check)
    assert len(asked) == 1
    assert sum(result["final_stacks"].values()) == 5080


def test_caller_not_asked_postflop_after_all_in_call():
    eng = make_engine([1000, 5000])
    state = eng.start_hand()
    state = eng.apply_action(state["seat_to_act"], {"action": "all_in"})
    assert state["type"] == "action_request"
    state = eng.apply_action(state["seat_to_act"], {"action": "call"})
    assert state["type"] == "hand_complete"
    assert len(state["community_cards"]) == 5


def test_bb_option_skipped_when_only_opponent_is_all_in():
    # UTG (seat 3) shoves 60 (< BB), everyone else folds to BB -> BB owes nothing
    # and is the only active player, so the hand runs out without asking BB.
    eng = make_engine([5000, 5000, 5000, 60])
    state = eng.start_hand()
    assert state["seat_to_act"] == 3
    state = eng.apply_action(3, {"action": "all_in"})
    state = eng.apply_action(state["seat_to_act"], {"action": "fold"})  # button
    state = eng.apply_action(state["seat_to_act"], {"action": "fold"})  # SB
    assert state["type"] == "hand_complete"


def test_run_out_emits_street_starts():
    eng = make_engine([1000, 5000], seed=3)
    state = eng.start_hand()
    state = eng.apply_action(state["seat_to_act"], {"action": "all_in"})
    state = eng.apply_action(state["seat_to_act"], {"action": "call"})
    streets = [e["street"] for e in state["events"] if e["type"] == "street_start"]
    assert streets == ["preflop", "flop", "turn", "river"]


# ---------------------------------------------------------------------------
# New state / result fields
# ---------------------------------------------------------------------------

def test_state_has_position_and_hand_fields():
    eng = make_engine([5000] * 4, dealer_seat=2, hand_num=17,
                      small_blind=25, big_blind=50, ante=5)
    state = eng.start_hand()
    assert state["hand_num"] == 17
    assert state["blinds"] == {"sb": 25, "bb": 50, "ante": 5}
    assert state["button_seat"] == 2
    assert state["sb_seat"] == 3
    assert state["bb_seat"] == 0
    assert state["seat_to_act"] == 1


def test_heads_up_positions():
    eng = make_engine([5000, 5000], dealer_seat=1)
    state = eng.start_hand()
    assert state["button_seat"] == 1
    assert state["sb_seat"] == 1
    assert state["bb_seat"] == 0
    assert state["seat_to_act"] == 1


def test_action_log_and_state_identify_bots_by_name():
    ids = ["alice", "bob", "carol"]
    eng = PokerEngine("h", ids, dealer_seat=0, ante=5)
    state = eng.start_hand()
    assert state["your_bot_id"] == ids[state["seat_to_act"]]
    state = eng.apply_action(state["seat_to_act"], {"action": "raise", "amount": 300})
    assert state["your_bot_id"] == ids[state["seat_to_act"]]
    for entry in state["action_log"]:
        assert set(entry) == {"seat", "bot_id", "action", "amount"}
        assert entry["bot_id"] == ids[entry["seat"]]
    assert [e["bot_id"] for e in state["action_log"] if e["action"] == "ante"] == ids
    blinds = {e["action"]: e["bot_id"] for e in state["action_log"]
              if e["action"] in ("small_blind", "big_blind")}
    assert blinds == {"small_blind": "bob", "big_blind": "carol"}
    assert state["action_log"][-1] == {"seat": 0, "bot_id": "alice",
                                       "action": "raise", "amount": 300}


def test_bot_id_stable_when_seats_shift():
    # Hand 1: three players; carol sits in seat 2.
    eng = PokerEngine("h1", ["alice", "bob", "carol"])
    eng.start_hand()
    assert eng.players[2].bot_id == "carol"
    # Hand 2: bob has busted, so the table is reseated and carol moves to seat 1.
    eng = PokerEngine("h2", ["alice", "carol"])
    state = eng.start_hand()
    carol = [e for e in state["action_log"] if e["bot_id"] == "carol"]
    assert carol and all(e["seat"] == 1 for e in carol)


def test_result_includes_starting_stacks_and_blinds():
    eng = make_engine([3000, 7000], small_blind=100, big_blind=200)
    state = eng.start_hand()
    result, _ = play_out(eng, state, lambda s: {"action": "fold"})
    assert result["starting_stacks"] == {"bot_0": 3000, "bot_1": 7000}
    assert result["blinds"] == {"sb": 100, "bb": 200, "ante": 0}
    assert result["button_seat"] == 0


def test_seeded_deck_is_deterministic():
    def deal(seed):
        eng = make_engine([5000] * 3, seed=seed)
        state = eng.start_hand()
        result, _ = play_out(eng, state, call_or_check)
        return result["community_cards"], {p.bot_id: [str(c) for c in p.hole_cards]
                                           for p in eng.players}
    assert deal(42) == deal(42)
    assert deal(42) != deal(43)


# ---------------------------------------------------------------------------
# Randomised stress test
# ---------------------------------------------------------------------------

def test_random_hands_never_ask_pointless_decisions():
    rng = random.Random(1234)
    actions = ["fold", "check", "call", "raise", "all_in", "nonsense"]
    for hand in range(3000):
        n = rng.randint(2, 9)
        bb = rng.choice([2, 10, 100, 400])
        sb = rng.randint(1, bb)
        ante = rng.choice([0, 0, 1, bb // 4])
        stacks = [rng.choice([1, sb, bb, rng.randint(1, 3 * bb), rng.randint(1, 200 * bb)])
                  for _ in range(n)]
        eng = make_engine(stacks, dealer_seat=rng.randrange(n), seed=hand,
                          small_blind=sb, big_blind=bb, ante=ante)
        total = sum(stacks)

        def policy(state):
            p = eng.players[state["seat_to_act"]]
            # Every request must be a real decision for an active player.
            assert not p.is_folded and not p.is_all_in and p.stack > 0, state
            active = [q for q in eng.players if q.is_active]
            assert len(active) >= 2 or state["amount_owed"] > 0, state
            act = rng.choice(actions)
            amount = rng.choice([0, state["min_raise_to"], rng.randint(0, p.stack * 2 + 1)])
            return {"action": act, "amount": amount}

        result, _ = play_out(eng, eng.start_hand(), policy)
        assert sum(result["final_stacks"].values()) == total
        assert result["starting_stacks"] == {f"bot_{i}": s for i, s in enumerate(stacks)}
