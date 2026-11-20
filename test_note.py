"""ワンナイト人狼(note版)のルールのテスト。 `python test_note.py` で実行できます。"""
import random

import note_rules as N


def game(roles, grave=("市民", "市民")):
    return N.NoteGame(roles=dict(roles), graveyard=list(grave))


def test_deck_sizes():
    for n in range(3, 9):
        deal, grave = N.make_deck(n, random.Random(n))
        assert len(deal) == n
        assert len(deal) + len(grave) == sum(N.card_counts(n).values())
        assert deal.count(N.WOLF) + grave.count(N.WOLF) == N.card_counts(n)[N.WOLF]


def test_seer_and_partner():
    g = game({1: N.WOLF, 2: N.WOLF, 3: N.SEER, 4: N.THIEF, 5: N.VILLAGER})
    assert g.look_player(1) == N.WOLF and g.look_player(5) == N.VILLAGER
    assert g.wolf_partners(1) == [2]
    assert g.wolf_partners(2) == [1]


def test_thief_swap_with_wolf():
    g = game({1: N.WOLF, 2: N.WOLF, 3: N.THIEF, 4: N.VILLAGER})
    g.swap(3, 1)               # 怪盗が人狼と交換
    assert g.roles[3] == N.WOLF and g.roles[1] == N.THIEF
    assert len(g.wolves()) == 2


def test_vote_normal_wolf_executed_village_wins():
    g = game({1: N.WOLF, 2: N.WOLF, 3: N.SEER, 4: N.VILLAGER})
    votes = {1: 3, 2: 3, 3: 1, 4: 1}   # 3 が2票
    votes = {2: 1, 3: 1, 4: 1, 1: 4}   # 1が3票 -> 人狼を処刑
    assert N.resolve_vote(g, votes) == ("village", [1])


def test_vote_scattered_no_execution_wolf_wins():
    g = game({1: N.WOLF, 2: N.WOLF, 3: N.SEER, 4: N.VILLAGER})
    votes = {1: 3, 2: 4, 3: 1, 4: 2}   # 全員バラバラ
    assert N.resolve_vote(g, votes) == ("wolf", [])


def test_vote_tie_both_executed():
    g = game({1: N.WOLF, 2: N.WOLF, 3: N.SEER, 4: N.VILLAGER})
    votes = {1: 3, 2: 3, 3: 4, 4: 4}   # 3と4が2票ずつ
    winner, executed = N.resolve_vote(g, votes)
    assert sorted(executed) == [3, 4]
    assert winner == "wolf"            # 人狼が含まれないので人狼の勝ち


def test_vote_tie_with_wolf_village_wins():
    g = game({1: N.WOLF, 2: N.WOLF, 3: N.SEER, 4: N.VILLAGER})
    votes = {1: 4, 2: 4, 3: 1, 4: 1}   # 1(人狼)と4 が2票ずつ
    winner, executed = N.resolve_vote(g, votes)
    assert sorted(executed) == [1, 4] and winner == "village"


def test_scattered_no_wolves_all_win():
    # 全員バラバラで、プレイヤーに人狼がいない -> 全員の勝ち(平和村)
    g = game({1: N.VILLAGER, 2: N.SEER, 3: N.VILLAGER}, grave=(N.WOLF, N.WOLF))
    assert N.resolve_vote(g, {1: 2, 2: 3, 3: 1}) == ("all", [])


def test_scattered_with_wolf_wolf_wins():
    g = game({1: N.WOLF, 2: N.SEER, 3: N.VILLAGER})
    assert N.resolve_vote(g, {1: 2, 2: 3, 3: 1}) == ("wolf", [])


def test_no_wolves_execution_wolf_wins():
    # 人狼がプレイヤーにいないとき、人間を処刑したら人狼の勝ち
    g = game({1: N.VILLAGER, 2: N.SEER, 3: N.VILLAGER}, grave=(N.WOLF, N.WOLF))
    assert N.resolve_vote(g, {1: 3, 2: 3, 3: 1}) == ("wolf", [3])


def test_madman_executed_alone_wolf_wins():
    # 狂人は人狼陣営。狂人だけ処刑されたら、人狼陣営(wolf)の勝ち
    g = game({1: N.WOLF, 2: N.MADMAN, 3: N.VILLAGER, 4: N.SEER})
    assert N.resolve_vote(g, {1: 2, 2: 2, 3: 2, 4: 1}) == ("wolf", [2])


def test_madman_is_wolf_team():
    assert N.team_of(N.MADMAN) == "wolf"


def test_eight_players_composition():
    assert N.card_counts(8) == {N.WOLF: 3, N.SEER: 1, N.THIEF: 1, N.MADMAN: 1, N.VILLAGER: 4}
    deal, grave = N.make_deck(8, random.Random(1))
    assert len(deal) == 8 and len(grave) == 2


def test_settle_inputs():
    g = game({1: N.WOLF, 2: N.SEER, 3: N.VILLAGER})
    assert N.settle_inputs(g, "village") == {1: False, 2: True, 3: True}
    assert N.settle_inputs(g, "wolf") == {1: True, 2: False, 3: False}
    assert N.settle_inputs(g, "all") == {1: True, 2: True, 3: True}


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("OK", name)
