"""追加役職(狂信者・囁き狂人・共有者・囁き共有者・大狼・猫又)のルールのテスト。 `python test_extra.py` で実行できます。"""
import points
from game import Game
from roles import ATTACKERS, EXTRA_ROLES, ROLES, WOLF_CHAT_ROLES, parse_composition, validate_composition


def make_game(comp):
    n = sum(comp.values())
    g = Game(1, 1, 0)
    for i in range(n):
        g.add_player(i, f"P{i}")
    g.custom_composition = dict(comp)
    assert g.begin() is None
    g.assign_roles({})
    return g


def set_roles(g, roles):
    for uid, r in roles.items():
        g.players[uid].role = r


def test_extra_roles_defined():
    assert set(EXTRA_ROLES) == {"狂信者", "囁き狂人", "共有者", "囁き共有者", "大狼", "静かな人狼", "平和主義者", "猫又"}
    assert all(ROLES[r].extra for r in EXTRA_ROLES)
    assert ROLES["大狼"].team == "wolf" and ROLES["大狼"].seen_as == "人間"
    assert ROLES["狂信者"].team == "wolf" and ROLES["狂信者"].seen_as == "人間"
    assert ROLES["囁き狂人"].team == "wolf"
    assert ROLES["共有者"].team == "village" and ROLES["囁き共有者"].team == "village"
    assert ROLES["猫又"].team == "village"


def test_parse_extra_roles():
    comp = parse_composition("人狼1 大狼1 猫又1 共有者2 市民5")
    assert comp["大狼"] == 1 and comp["猫又"] == 1
    assert validate_composition(comp, 10, False) is None


def test_extra_roles_not_allowed_in_one_night():
    assert validate_composition({"人狼": 1, "猫又": 1, "市民": 1}, 3, True)


def test_madman_types_do_not_count_as_wolves():
    # 人狼(大狼)がいなければ検証でエラー
    assert validate_composition({"狂信者": 1, "囁き狂人": 1, "市民": 6}, 8, False)
    assert validate_composition({"人狼": 1, "狂信者": 1, "囁き狂人": 1, "市民": 5}, 8, False) is None


def test_great_wolf_is_attacker_and_counts_for_win():
    g = make_game({"人狼": 1, "大狼": 1, "市民": 6})
    set_roles(g, {0: "人狼", 1: "大狼", **{i: "市民" for i in range(2, 8)}})
    assert g.check_winner() is None
    for uid in (2, 3, 4, 5):
        g.kill(uid)
    # 生存: 人狼・大狼・市民2人 -> 人狼側2人 >= 市民2人
    assert g.check_winner() == "wolf"


def test_village_wins_when_all_attackers_dead():
    g = make_game({"人狼": 1, "大狼": 1, "市民": 6})
    set_roles(g, {0: "人狼", 1: "大狼", **{i: "市民" for i in range(2, 8)}})
    g.kill(0)
    assert g.check_winner() is None
    g.kill(1)
    assert g.check_winner() == "village"


def test_attack_targets_exclude_both_wolf_types():
    g = make_game({"人狼": 1, "大狼": 1, "狂信者": 1, "市民": 5})
    set_roles(g, {0: "人狼", 1: "大狼", 2: "狂信者", **{i: "市民" for i in range(3, 8)}})
    names = {p.role for p in g.night_targets(0, "attack")}
    assert "人狼" not in names and "大狼" not in names and "狂信者" in names


def test_seer_sees_great_wolf_as_human():
    g = make_game({"人狼": 1, "大狼": 1, "占い師": 1, "市民": 5})
    set_roles(g, {0: "人狼", 1: "大狼", 2: "占い師", **{i: "市民" for i in range(3, 8)}})
    assert g.record_night_action(2, "divine", 1) == "人間"
    assert g.record_night_action(2, "divine", 0) == "人狼"


def test_great_wolf_attack_counts_kills_and_resolves():
    g = make_game({"人狼": 1, "大狼": 1, "市民": 6})
    set_roles(g, {0: "人狼", 1: "大狼", **{i: "市民" for i in range(2, 8)}})
    g.reset_night()
    g.record_night_action(1, "attack", 4)
    res = g.resolve_night()
    assert res.victim == 4
    g.apply_night(res)
    assert g.players[0].kills == 1 and g.players[1].kills == 1


def test_all_wolf_roles_are_attackers_set():
    assert set(ATTACKERS) == {"人狼", "大狼", "静かな人狼"}
    assert "静かな人狼" in WOLF_CHAT_ROLES
    assert ROLES["静かな人狼"].team == "wolf" and ROLES["静かな人狼"].seen_as == "人狼"
    assert ROLES["大狼"].night_action == "attack"


def test_extra_role_win_reward_multiplier():
    t = points.FULL
    r = points.Result(uid=1, team="village", team_won=True, nights_survived=0, survived_to_end=False,
                      quit_early=False, win_mult=points.EXTRA_ROLE_WIN_MULT)
    assert points.raw_payout(r, t) == round(120 * 1.5) == 180
    r2 = points.Result(uid=2, team="wolf", team_won=True, nights_survived=0, survived_to_end=False,
                       quit_early=False, win_mult=points.EXTRA_ROLE_WIN_MULT)
    assert points.raw_payout(r2, t) == 300
    lost = points.Result(uid=3, team="village", team_won=False, nights_survived=0, survived_to_end=False,
                         quit_early=False, win_mult=1.5)
    assert points.raw_payout(lost, t) == 0                       # 負けたら増えない
    quit_ = points.Result(uid=4, team="village", team_won=True, nights_survived=0, survived_to_end=False,
                          quit_early=True, win_mult=1.5)
    assert points.raw_payout(quit_, t) == 0                      # 突然死は勝利報酬なし


def test_settlement_applies_multiplier_only_to_extra_roles():
    g = make_game({"人狼": 1, "大狼": 1, "猫又": 1, "市民": 5})
    set_roles(g, {0: "人狼", 1: "大狼", 2: "猫又", **{i: "市民" for i in range(3, 8)}})
    s = g.settlement("village", set(range(8)))
    # 猫又(追加役職・村人陣営)は、市民より多く受け取る(勝利報酬が1.5倍。生還20ptと上限の調整は別)
    assert s.final[2] > s.final[3] > 0
    assert s.final[3] == s.final[4] == s.final[5]                 # 市民どうしは同じ


def test_peace_vote_only_while_pacifist_alive():
    from collections import Counter
    g = make_game({"人狼": 1, "平和主義者": 1, "市民": 6})
    set_roles(g, {0: "人狼", 1: "平和主義者", **{i: "市民" for i in range(2, 8)}})
    assert g.peace_available()
    g.kill(1)
    assert not g.peace_available()


def test_peace_wins_only_when_strictly_more_than_top_player():
    from collections import Counter
    from game import Game
    g = make_game({"人狼": 1, "平和主義者": 1, "市民": 6})
    assert g.peace_wins(Counter({3: 2}), 3)          # 誰も3票 > 最多2票 -> 処刑なし
    assert not g.peace_wins(Counter({3: 2}), 2)      # 同数 -> プレイヤーだけで決選
    assert not g.peace_wins(Counter(), 0)
    assert g.target_name(-1) == "誰も追放しない"


def test_sharer_pairs_are_same_role_and_know_only_partner():
    for _ in range(50):
        g = make_game({"人狼": 1, "共有者": 4, "囁き共有者": 2, "市民": 3})
        assert set(g.partner) == {u for u, p in g.players.items() if p.role in ("共有者", "囁き共有者")}
        for a, b in g.partner.items():
            assert g.partner[b] == a and a != b
            assert g.players[a].role == g.players[b].role      # 混合の組はない


def test_sharer_pair_count_must_be_even():
    assert validate_composition({"人狼": 1, "共有者": 1, "市民": 4}, 6, False)
    assert validate_composition({"人狼": 1, "囁き共有者": 3, "市民": 2}, 6, False)
    assert validate_composition({"人狼": 1, "共有者": 2, "囁き共有者": 2, "市民": 2}, 7, False) is None


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("OK", t.__name__)
